"""
Запись экрана для «запиши это видео»: картинка экрана → .mp4, потом склейка со звуком записи.

Нужен ffmpeg: берётся из системы, а если его нет — из пакета imageio-ffmpeg (ставится сам, ~30 МБ).
Способы захвата: ddagrab (Desktop Duplication — быстро, правильно на экранах с масштабом 125–200 %),
если не вышло — gdigrab. 15 кадров в секунду, ширина 1280 — примерно 150–300 МБ в час.
Файл пишется «кусочками» (fragmented mp4): даже если Atlas упадёт, записанное откроется.
"""
import os
import shutil
import subprocess
import sys
import threading
import time

FPS = int(os.getenv("SCREEN_FPS") or 15)
WIDTH = int(os.getenv("SCREEN_WIDTH") or 1280)
_NOWIN = 0x08000000 if os.name == "nt" else 0            # без чёрного окна консоли


def ffmpeg_exe() -> str:
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
    except ImportError:
        print("[записи] ставлю ffmpeg (imageio-ffmpeg) для записи экрана…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"], check=False)
        try:
            import imageio_ffmpeg
        except ImportError:
            return ""
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


def _sources() -> list:
    """Варианты захвата экрана: [(название, аргументы входа)]."""
    scale = f"scale='min({WIDTH},iw)':-2"
    return [
        ("ddagrab", ["-f", "lavfi", "-i", f"ddagrab=output_idx=0:framerate={FPS}:draw_mouse=1,hwdownload,format=bgra,"
                                          f"{scale}"]),
        ("gdigrab", ["-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "1", "-i", "desktop", "-vf", scale]),
    ]


class ScreenRecorder:
    def __init__(self, out_path: str, exe: str = None, sources: list = None):
        self.out, self.exe, self.sources = out_path, exe, sources
        self.proc, self.t0, self.how, self.error = None, None, "", ""

    def start(self, wait: float = 6.0) -> bool:
        """Запустить и дождаться первых кадров. self.t0 — когда на самом деле снят первый кадр."""
        exe = self.exe or ffmpeg_exe()
        if not exe:
            self.error = "нет ffmpeg"
            return False
        for name, args in (self.sources or _sources()):
            cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", *args,
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-g", str(FPS * 4),
                   "-movflags", "+frag_keyframe+empty_moov+default_base_moof", self.out]
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             creationflags=_NOWIN)
            except Exception as e:
                self.error = str(e)
                continue
            started = threading.Event()
            threading.Thread(target=self._read_progress, args=(started,), daemon=True).start()
            if started.wait(wait) and self.proc.poll() is None:
                self.how = name
                print(f"[записи] экран пишется ({name}, {FPS} к/с)")
                return True
            err = self._stop_proc(kill=True)
            self.error = (err or "ffmpeg не начал запись")[-300:]
            print(f"[записи] экран через {name} не пишется: {self.error.strip()[:160]}")
        return False

    def _read_progress(self, started: threading.Event) -> None:
        """ffmpeg -progress: out_time_us — сколько уже снято. Начало = сейчас − снятое."""
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("out_time_us=") and self.t0 is None:
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if us > 0:
                    self.t0 = time.time() - us / 1e6
                    started.set()

    def _stop_proc(self, kill: bool = False) -> str:
        p, self.proc = self.proc, None
        if p is None:
            return ""
        try:
            if not kill and p.poll() is None:
                p.stdin.write(b"q")                         # ffmpeg закрывает файл аккуратно
                p.stdin.flush()
                p.wait(timeout=15)
        except Exception:
            pass
        if p.poll() is None:
            p.kill()
            p.wait(timeout=5)
        try:
            return p.stderr.read().decode("utf-8", "ignore")
        except Exception:
            return ""

    def stop(self) -> bool:
        self._stop_proc()
        return os.path.exists(self.out) and os.path.getsize(self.out) > 1000


def mux(video: str, audio: str, out: str, offset: float, exe: str = None) -> bool:
    """Картинка + звук → один .mp4. offset — на сколько секунд видео началось позже звука."""
    exe = exe or ffmpeg_exe()
    if not exe or not os.path.exists(video):
        return False
    shift = ["-itsoffset", f"{abs(offset):.3f}"]
    vin = (shift if offset > 0.02 else []) + ["-i", video]
    ain = (shift if offset < -0.02 else []) + ["-i", audio]
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", *vin, *ain, "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=1800, creationflags=_NOWIN)
    except Exception as e:
        print(f"[записи] склейка видео и звука: {e}")
        return False
    if r.returncode != 0:
        print(f"[записи] склейка видео и звука: {r.stderr.decode('utf-8', 'ignore')[-200:]}")
        return False
    return os.path.exists(out) and os.path.getsize(out) > 1000
