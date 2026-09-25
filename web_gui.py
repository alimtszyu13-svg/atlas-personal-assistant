import webview
import psutil
from ui_state import shared_state
import time


class Api:
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
        mics = [d['name'] for d in sd.query_devices()
                if d['max_input_channels'] > 0 and "переназначение" not in d['name'].lower()]

        lang = get_response_language()
        voices = list_voice_choices(lang)
        current_voice = current_voice_choice(lang)

        return {
            "microphones": mics,
            "speakers": speakers,
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
        from voice import set_speaker
        set_speaker(name)

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
            from ai_brain import cancel_current_task
            cancel_current_task()
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
        import os
        try:
            from browser_agent import browser_close
            browser_close()
        except Exception:
            pass
        os._exit(0)

    def get_state(self) -> dict:
        speech_start = shared_state.get("speech_start_time", 0)
        speech_elapsed = time.time() - speech_start if speech_start else 0

        battery = psutil.sensors_battery()
        disk = psutil.disk_usage("/")
        uptime_hours = round((time.time() - psutil.boot_time()) / 3600, 1)

        return {
            "state": shared_state.get("state", "idle"),
            "text": shared_state.get("text", ""),
            "theme": shared_state.get("theme", "dark"),
            "accent_color": shared_state.get("accent_color", "#22d3ee"),
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
        }

class WebGUI:
    def __init__(self, shared_state: dict):
        self.shared_state = shared_state
        self.api = Api()
        self.window = None
        self._is_hidden = False

    def run(self):
        self.window = webview.create_window(
            "ATLAS", "atlas_ui.html", js_api=self.api,
            fullscreen=True, background_color="#050810"
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