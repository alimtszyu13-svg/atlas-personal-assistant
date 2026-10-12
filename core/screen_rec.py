"""
Запись экрана для «запиши это видео»: картинка экрана → .mp4, потом склейка со звуком записи.

Нужен ffmpeg: берётся из системы, а если его нет — из пакета imageio-ffmpeg (ставится сам, ~30 МБ).
Способы захвата: ddagrab (Desktop Duplication — быстро, правильно на экранах с масштабом 125–200 %),
если не вышло — gdigrab. Сжатие — видеокартой (NVIDIA / Intel / AMD), если есть, иначе процессором.

SCREEN_QUALITY в .env:
    high   (по умолчанию) — родное разрешение экрана (до 2560 по ширине), 30 к/с, чёткий текст
    medium — до 1920, 24 к/с, файлы примерно вдвое меньше
    low    — 1280, 15 к/с, самые маленькие файлы
Файл пишется «кусочками» (fragmented mp4): даже если Atlas упадёт, записанное откроется.
"""
import os
import shutil
import subprocess
import sys
import threading
import time

_PRESETS = {"high": (30, 2560, 21), "medium": (24, 1920, 25), "low": (15, 1280, 29)}
FPS, WIDTH, QUALITY = _PRESETS.get((os.getenv("SCREEN_QUALITY") or "high").strip().lower(), _PRESETS["high"])
FPS = int(os.getenv("SCREEN_FPS") or FPS)
WIDTH = int(os.getenv("SCREEN_WIDTH") or WIDTH)
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


def _encoders() -> list:
    """Чем сжимать: видеокарта, если есть, — меньше нагрузка на процессор; иначе libx264."""
    q = str(QUALITY)
    return [
        ("nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", q, "-b:v", "0"]),
        ("qsv", ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", q]),
        ("amf", ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", q, "-qp_p", str(int(q) + 2)]),
        ("x264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", q, "-tune", "zerolatency"]),
    ]


def _sources() -> list:
    """Варианты захвата экрана: [(название, аргументы входа)]."""
    scale = f"scale='min({WIDTH},iw)':-2"
    return [
        ("ddagrab", ["-f", "lavfi", "-i", f"ddagrab=output_idx=0:framerate={FPS}:draw_mouse=1,hwdownload,format=bgra,"
                                          f"{scale}"]),
        ("gdigrab", ["-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "1", "-i", "desktop", "-vf", scale]),
    ]


class ScreenRecorder:
    def __init__(self, out_path: str, exe: str = None, sources: list = None, encoders: list = None):
        self.out, self.exe, self.sources, self.encoders = out_path, exe, sources, encoders
        self.proc, self.t0, self.how, self.error = None, None, "", ""

    def start(self, wait: float = 6.0) -> bool:
        """Запустить и дождаться первых кадров. self.t0 — когда на самом деле снят первый кадр."""
        exe = self.exe or ffmpeg_exe()
        if not exe:
            self.error = "нет ffmpeg"
            return False
        encoders = self.encoders if getattr(self, "encoders", None) is not None else _encoders()
        for name, args in (self.sources or _sources()):
            for enc, eargs in encoders:
                cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", *args,
                       *eargs, "-pix_fmt", "yuv420p", "-g", str(FPS * 2),
                       "-movflags", "+frag_keyframe+empty_moov+default_base_moof", self.out]
                if self._try(cmd, wait):
                    self.how = f"{name}, {enc}"
                    print(f"[записи] экран пишется ({self.how}, {FPS} к/с)")
                    return True
                if "Filter not found" in self.error or "No such filter" in self.error or \
                        "Could not find" in self.error and "input" in self.error.lower():
                    break                                       # не тот захват — другой способ, а не другой кодек
        print(f"[записи] экран не пишется: {self.error.strip()[:160]}")
        return False

    def _try(self, cmd: list, wait: float) -> bool:
        self.t0 = None
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=_NOWIN)
        except Exception as e:
            self.error = str(e)
            return False
        started = threading.Event()
        threading.Thread(target=self._read_progress, args=(started,), daemon=True).start()
        deadline = time.time() + wait
        while time.time() < deadline and not started.is_set() and self.proc.poll() is None:
            started.wait(0.1)                                   # не ждём зря, если ffmpeg уже вышел с ошибкой
        if started.is_set() and self.proc.poll() is None:
            return True
        err = self._stop_proc(kill=True)
        self.error = (err or "ffmpeg не начал запись")[-300:]
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
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=1800, creationflags=_NOWIN)
    except Exception as e:
        print(f"[записи] склейка видео и звука: {e}")
        return False
    if r.returncode != 0:
        print(f"[записи] склейка видео и звука: {r.stderr.decode('utf-8', 'ignore')[-200:]}")
        return False
    return os.path.exists(out) and os.path.getsize(out) > 1000


# ---------------------------------------------------------------------------
# Камера: пишется кусками (пока включена), в конце ложится на видео экрана окошком в углу.
# Без экрана — камера сама становится картинкой (в паузах — чёрный фон).
# ---------------------------------------------------------------------------
def cameras(exe: str = None) -> list:
    """Названия веб-камер Windows (DirectShow)."""
    if os.name != "nt":
        return []
    exe = exe or ffmpeg_exe()
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, timeout=15, creationflags=_NOWIN)
    except Exception:
        return []
    import re as _re
    text = r.stderr.decode("utf-8", "ignore")
    return [m.group(1) for m in _re.finditer(r'"([^"]+)"\s*\(video\)', text)]


def camera_recorder(out_path: str, name: str = "", exe: str = None) -> "ScreenRecorder":
    name = name or (os.getenv("CAMERA_NAME") or "").strip() or next(iter(cameras(exe)), "")
    rec = ScreenRecorder(out_path, exe=exe, sources=[("камера", ["-f", "dshow", "-rtbufsize", "200M",
                                                                 "-i", f"video={name}"])] if name else [])
    if not name:
        rec.error = "веб-камера не найдена"
    return rec


def video_size(path: str, exe: str = None):
    """(ширина, высота) видео или None."""
    import re as _re
    exe = exe or ffmpeg_exe()
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, timeout=30, creationflags=_NOWIN)
    except Exception:
        return None
    m = _re.search(r"Video: .*?(\d{3,5})x(\d{3,5})", r.stderr.decode("utf-8", "ignore"))
    return (int(m.group(1)), int(m.group(2))) if m else None


def compose(screen: str, cams: list, audio: str, out: str, audio_t0: float, screen_t0: float = None,
            duration: float = None, exe: str = None) -> bool:
    """Экран (или чёрный фон) + куски камеры [(файл, когда начался)] + звук → один .mp4.
    Время каждого куска считается от начала звука (audio_t0)."""
    exe = exe or ffmpeg_exe()
    cams = [(p, t) for p, t in cams if p and os.path.exists(p) and os.path.getsize(p) > 1000 and t]
    if not exe or not cams:
        return False
    args, filt = [exe, "-hide_banner", "-loglevel", "error", "-y"], []
    if screen and os.path.exists(screen):
        off = (screen_t0 or audio_t0) - audio_t0
        args += (["-itsoffset", f"{off:.3f}"] if off > 0.02 else []) + ["-i", screen]
        size = video_size(screen, exe) or (1920, 1080)
        cw = max(240, int(size[0] * 0.22) // 2 * 2)             # окошко камеры — 22 % ширины экрана
        place = "x=W-w-24:y=H-h-24"
        fit = f"scale={cw}:-2"
    else:
        dur = max(1.0, duration or 1.0)
        args += ["-f", "lavfi", "-i", f"color=c=black:s=1280x720:r=30:d={dur:.2f}"]
        place = "x=(W-w)/2:y=(H-h)/2"
        fit = "scale=1280:720:force_original_aspect_ratio=decrease"
    for p, t in cams:
        off = max(0.0, t - audio_t0)
        args += ["-itsoffset", f"{off:.3f}", "-i", p]
    have_audio = bool(audio and os.path.exists(audio))
    if have_audio:
        args += ["-i", audio]
    last = "0:v"
    for i in range(len(cams)):
        filt.append(f"[{i + 1}:v]{fit},format=yuv420p[c{i}]")
        filt.append(f"[{last}][c{i}]overlay={place}:eof_action=pass[v{i}]")
        last = f"v{i}"
    args += ["-filter_complex", ";".join(filt), "-map", f"[{last}]"]
    if have_audio:
        args += ["-map", f"{len(cams) + 1}:a", "-c:a", "aac", "-b:a", "192k"]
    tail = ["-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    for enc, eargs in _encoders():                               # видеокарта, если есть, — быстро
        try:
            r = subprocess.run(args + eargs + tail, capture_output=True, timeout=6 * 3600, creationflags=_NOWIN)
        except Exception as e:
            print(f"[записи] камера + экран ({enc}): {e}")
            continue
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
            return True
        print(f"[записи] камера + экран ({enc}): {r.stderr.decode('utf-8', 'ignore')[-160:]}")
    return False
