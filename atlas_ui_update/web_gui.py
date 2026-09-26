import os
import re
import sqlite3
import subprocess
import time

import psutil
import webview

from ui_state import shared_state

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _db_path(module, default_name: str) -> str:
    """Путь к базе модуля: берём из самого модуля, если он его объявляет,
    иначе — файл рядом с проектом (так их создают core/memory.py и core/missions.py)."""
    for attr in ("DB_PATH", "DB", "DB_FILE", "MEMORY_DB", "MISSIONS_DB"):
        p = getattr(module, attr, None)
        if isinstance(p, str) and p.endswith(".db"):
            return p if os.path.isabs(p) else os.path.join(_ROOT, p)
    return os.path.join(_ROOT, default_name)


def _fmt_ts(value) -> str:
    """Время из базы (число или строка) → «26.09 21:04»."""
    try:
        return time.strftime("%d.%m %H:%M", time.localtime(float(value)))
    except (TypeError, ValueError):
        return str(value or "")[:16]


class Api:
    # ------------------------------------------------------------------
    # Всё, что было в прежнем интерфейсе — без изменений
    # ------------------------------------------------------------------
    def get_memories(self) -> list:
        from database import get_all_memories
        return get_all_memories()

    def delete_memory_ui(self, memory_id: int) -> None:
        from database import delete_memory_by_id
        delete_memory_by_id(memory_id)

    def get_trace(self) -> list:
        from database import get_recent_tasks
        return get_recent_tasks()

    def set_language_ui(self, lang: str) -> None:
        from voice import set_response_language
        set_response_language(lang)

    def get_language(self) -> str:
        from voice import get_response_language
        return get_response_language()

    def get_audio_options(self) -> dict:
        import sounddevice as sd
        from voice import get_response_language, list_voice_choices, current_voice_choice
        try:
            from pygame._sdl2 import audio as sdl2_audio
            speakers = sdl2_audio.get_audio_device_names(False)
        except Exception:
            speakers = []
        devices = sd.query_devices()
        mics = [d['name'] for d in devices
                if d['max_input_channels'] > 0 and "переназначение" not in d['name'].lower()]

        # какие устройства выбраны сейчас — чтобы список показывал их, а не первый попавшийся
        current_mic = None
        try:
            dev = sd.default.device
            idx = dev[0] if isinstance(dev, (list, tuple)) else dev
            if idx is not None and idx >= 0:
                current_mic = devices[idx]['name']
        except Exception:
            pass
        if current_mic not in mics:
            try:
                current_mic = sd.query_devices(kind="input")["name"]
            except Exception:
                current_mic = None

        lang = get_response_language()
        voices = list_voice_choices(lang)
        current_voice = current_voice_choice(lang)

        return {
            "microphones": mics,
            "speakers": speakers,
            "current_microphone": current_mic if current_mic in mics else (mics[0] if mics else None),
            "current_speaker": shared_state.get("speaker_name") or (speakers[0] if speakers else None),
            "voices": voices,
            "current_voice": current_voice,
            "always_listening": shared_state.get("always_listening", False),
            "language": lang,
        }

    def set_voice_ui(self, name: str) -> None:
        from voice import choose_voice
        choose_voice(name)

    def set_microphone_ui(self, name: str) -> None:
        from voice import set_microphone
        set_microphone(name)

    def set_speaker_ui(self, name: str) -> None:
        from voice import set_speaker, output_is_headphones
        shared_state["speaker_name"] = name
        set_speaker(name)
        print(f"[audio] вывод: {name} → {'наушники' if output_is_headphones() else 'колонки'}")

    def set_always_listening_ui(self, enabled: bool) -> None:
        from listening_mode import set_always_listening
        set_always_listening(enabled)

    def push_to_talk(self) -> None:
        from voice import trigger_push_to_talk
        trigger_push_to_talk()

    def get_today_agenda(self) -> str:
        try:
            from calendar_control import list_today_events
            return list_today_events()
        except Exception as e:
            return f"Calendar error: {e}"

    def get_notes_ui(self) -> str:
        try:
            from notes import list_notes
            return list_notes()
        except Exception as e:
            return f"Notes error: {e}"

    def locate_file_ui(self, name: str) -> str:
        from file_control import locate_file
        return locate_file(name)

    def open_file_ui(self, name: str) -> str:
        from file_control import open_file
        return open_file(name)

    def send_text_command(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.lower().strip(" .!") in ("стоп", "stop", "отмена", "cancel", "хватит"):
            self.stop_speaking_ui()
            return
        shared_state["manual_queue"].append(text)

    def cancel_task(self) -> None:
        from ai_brain import cancel_current_task
        cancel_current_task()

    def set_theme(self, theme: str) -> None:
        shared_state["theme"] = theme

    def set_accent(self, color: str) -> None:
        shared_state["accent_color"] = color

    def confirm_quit(self) -> None:
        """
        Вызывается из JS после анимации выключения.
        os._exit(0) завершает процесс мгновенно и безусловно — в отличие от
        window.destroy(), которому для многих бэкендов pywebview нужна
        точная синхронизация с GUI-потоком, что на практике оказалось
        ненадёжным (зависание на середине анимации). Раз вся полезная
        работа (голос уже остановлен, фраза уже сказана) на этот момент
        сделана — жёсткий выход безопасен и предсказуем.

        Перед выходом закрываем браузер Atlas'а (если был открыт) — иначе
        Playwright не успевает корректно завершить свой Node-процесс до
        os._exit(0), и в консоли остаётся некрасивый EPIPE crash-лог.
        """
        try:
            from browser_agent import browser_close
            browser_close()
        except Exception:
            pass
        os._exit(0)

    # ------------------------------------------------------------------
    # Главный опрос интерфейса (~5 раз в секунду)
    # ------------------------------------------------------------------
    _missions_cache = {"t": 0.0, "n": 0}

    def _missions_running(self) -> int:
        """Сколько миссий идёт сейчас. Кешируем на секунду — опрос частый."""
        now = time.time()
        if now - self._missions_cache["t"] < 1.0:
            return self._missions_cache["n"]
        n = 0
        try:
            from core import missions
            conn = sqlite3.connect(_db_path(missions, "missions.db"))
            n = conn.execute("SELECT COUNT(*) FROM missions WHERE status = 'running'").fetchone()[0]
            conn.close()
        except Exception:
            pass
        self._missions_cache.update(t=now, n=n)
        return n

    def get_state(self) -> dict:
        speech_start = shared_state.get("speech_start_time", 0)
        speech_elapsed = time.time() - speech_start if speech_start else 0

        battery = psutil.sensors_battery()
        disk = psutil.disk_usage("/")
        uptime_hours = round((time.time() - psutil.boot_time()) / 3600, 1)
        try:
            from voice import get_response_language
            lang = get_response_language()
        except Exception:
            lang = "ru"

        return {
            "state": shared_state.get("state", "idle"),
            "text": shared_state.get("text", ""),
            "theme": shared_state.get("theme", "dark"),
            "accent_color": shared_state.get("accent_color", "#86D6FF"),
            "language": lang,
            "chat_history": shared_state.get("chat_history", [])[-30:],
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "battery_percent": battery.percent if battery else None,
            "battery_charging": battery.power_plugged if battery else None,
            "disk_percent": disk.percent,
            "uptime_hours": uptime_hours,
            "should_quit": shared_state.get("should_quit", False),
            "speech_envelope": shared_state.get("speech_envelope", []),
            "speech_duration": shared_state.get("speech_duration", 0),
            "speech_elapsed": speech_elapsed,
            "mic_level": shared_state.get("mic_level", 0.0),
            "missions_running": self._missions_running(),
            "notif_count": shared_state.get("notif_seq", 0),
        }

    # ------------------------------------------------------------------
    # Новое: кнопка «Стоп» — замолчать и отменить текущую задачу
    # ------------------------------------------------------------------
    def stop_speaking_ui(self) -> None:
        try:
            from voice import _stop_speaking
            import pygame
            _stop_speaking.set()
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception as e:
            print(f"[stop] {e}")
        try:
            from ai_brain import cancel_current_task
            cancel_current_task()
        except Exception as e:
            print(f"[stop] {e}")

    # ------------------------------------------------------------------
    # Новое: поиск файлов по содержимому (раздел «Файлы»)
    # ------------------------------------------------------------------
    def search_files_ui(self, query: str) -> list:
        import file_search
        query = (query or "").strip()
        if not query:
            return []
        text = file_search.search_file_content(query)
        lines = [l for l in str(text).splitlines() if re.match(r"^\s*\d+\.", l)]
        paths = list(getattr(file_search, "_last_results", []) or [])
        out = []
        for i, line in enumerate(lines):
            m = re.search(r"«(.+?)»", line)
            snippet = m.group(1) if m else ""
            if i < len(paths):
                path = paths[i]
            else:                               # запасной разбор: «имя — в папке путь»
                mm = re.match(r"^\s*\d+\.\s*(.+?)\s+—\s+в папке\s+(.+?)(?:\.\s+Фрагмент:|$)", line)
                if not mm:
                    continue
                path = os.path.join(mm.group(2).strip(), mm.group(1).strip())
            out.append({"name": os.path.basename(path), "folder": os.path.dirname(path),
                        "path": path, "snippet": snippet})
        return out

    def open_path_ui(self, path: str) -> str:
        try:
            os.startfile(path)
            return "ok"
        except Exception as e:
            return str(e)

    def show_in_folder_ui(self, path: str) -> str:
        try:
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
            return "ok"
        except Exception as e:
            return str(e)

    # ------------------------------------------------------------------
    # Новое: миссии
    # ------------------------------------------------------------------
    def get_missions_ui(self) -> list:
        from core import missions
        try:
            conn = sqlite3.connect(_db_path(missions, "missions.db"))
            rows = conn.execute("SELECT id, goal, status, progress FROM missions "
                                "ORDER BY id DESC LIMIT 12").fetchall()
            conn.close()
        except Exception as e:
            print(f"[missions ui] {e}")
            return []
        return [{"id": r[0], "goal": str(r[1] or "").split("\n")[0][:140],
                 "status": r[2] or "done", "progress": str(r[3] or "")[:160]} for r in rows]

    def cancel_mission_ui(self, mission_id: int) -> bool:
        from core import missions
        fn = getattr(missions, "cancel", None)
        if fn is None:
            return False
        fn(int(mission_id))
        return True

    def start_mission_ui(self, goal: str):
        from core import missions
        goal = (goal or "").strip()
        if not goal:
            return None
        return missions.start(goal)

    # ------------------------------------------------------------------
    # Новое: уведомления и лимиты
    # ------------------------------------------------------------------
    def get_notifications_ui(self) -> list:
        return list(shared_state.get("notifications", []))[-60:]

    def get_limits_ui(self) -> dict:
        try:
            from core import llm_gateway
            return llm_gateway.snapshot()
        except Exception as e:
            print(f"[limits ui] {e}")
            return {"models": {}, "last": {}}

    # ------------------------------------------------------------------
    # Новое: граф знаний и эпизоды памяти
    # ------------------------------------------------------------------
    def _memory_conn(self):
        from core import memory
        return sqlite3.connect(_db_path(memory, "memory.db"))

    def get_graph_ui(self) -> list:
        try:
            conn = self._memory_conn()
            rows = conn.execute(
                "SELECT e.rowid, s.name, e.rel, d.name, e.active FROM edges e "
                "JOIN nodes s ON s.id = e.src JOIN nodes d ON d.id = e.dst "
                "ORDER BY e.active DESC, e.updated DESC LIMIT 80").fetchall()
            conn.close()
        except Exception as e:
            print(f"[graph ui] {e}")
            return []
        return [{"id": r[0], "s": r[1], "rel": str(r[2]).replace("_", " "), "o": r[3],
                 "active": bool(r[4])} for r in rows]

    def forget_fact_ui(self, edge_id: int) -> bool:
        try:
            conn = self._memory_conn()
            conn.execute("UPDATE edges SET active = 0 WHERE rowid = ?", (int(edge_id),))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[graph ui] {e}")
            return False

    def get_episodes_ui(self) -> list:
        try:
            conn = self._memory_conn()
            rows = conn.execute("SELECT ended, summary FROM episodes "
                                "WHERE summary IS NOT NULL AND summary != '' "
                                "ORDER BY ended DESC LIMIT 20").fetchall()
            conn.close()
        except Exception as e:
            print(f"[episodes ui] {e}")
            return []
        return [{"when": _fmt_ts(r[0]), "summary": r[1]} for r in rows]


class WebGUI:
    def __init__(self, shared_state: dict):
        self.shared_state = shared_state
        self.api = Api()
        self.window = None
        self._is_hidden = False

    def run(self):
        self.window = webview.create_window(
            "ATLAS", "atlas_ui.html", js_api=self.api,
            fullscreen=True, background_color="#02040A"
        )
        webview.start(debug=True)

    def show_window(self):
        if self.window:
            self.window.show()

    def quit_app(self):
        self.shared_state["should_quit"] = True
        if self.window:
            self.window.destroy()

    def toggle_visibility(self) -> None:
        if self.window is None:
            print("[window toggle] window object is None")
            return

        print(f"[window toggle] hiding={not self._is_hidden}, methods available: "
              f"hide={hasattr(self.window, 'hide')}, minimize={hasattr(self.window, 'minimize')}")

        try:
            if self._is_hidden:
                self.window.show()
            else:
                self.window.hide()
        except Exception as e:
            print(f"[window toggle] hide/show failed ({e}), trying minimize/restore instead")
            try:
                if self._is_hidden:
                    self.window.restore()
                else:
                    self.window.minimize()
            except Exception as e2:
                print(f"[window toggle] minimize/restore also failed: {e2}")
                return

        self._is_hidden = not self._is_hidden
