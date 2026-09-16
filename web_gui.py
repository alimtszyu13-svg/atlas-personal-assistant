import webview
import psutil
from ui_state import shared_state
import time


class Api:
    def set_language_ui(self, lang: str) -> None:
        from voice import set_response_language
        set_response_language(lang)

    def get_language(self) -> str:
        from voice import get_response_language
        return get_response_language()
    
    def get_audio_options(self) -> dict:
        import sounddevice as sd
        from voice import VOICE_OPTIONS, TTS_VOICE, get_response_language, ELEVENLABS_VOICE_OPTIONS, _elevenlabs_voice
        try:
            from pygame._sdl2 import audio as sdl2_audio
            speakers = sdl2_audio.get_audio_device_names(False)
        except Exception:
            speakers = []
        mics = [d['name'] for d in sd.query_devices()
                if d['max_input_channels'] > 0 and "переназначение" not in d['name'].lower()]

        lang = get_response_language()
        if lang == "ru":
            voices = list(ELEVENLABS_VOICE_OPTIONS["male"].keys()) + list(ELEVENLABS_VOICE_OPTIONS["female"].keys())
            current_voice = _elevenlabs_voice["name"]
        else:
            voices = VOICE_OPTIONS["male"] + VOICE_OPTIONS["female"]
            current_voice = TTS_VOICE

        return {
            "microphones": mics,
            "speakers": speakers,
            "voices": voices,
            "current_voice": current_voice,
            "always_listening": shared_state.get("always_listening", False),
            "language": lang,
        }

    def set_voice_ui(self, name: str) -> None:
        from voice import set_voice, set_elevenlabs_voice, get_response_language
        if get_response_language() == "ru":
            set_elevenlabs_voice(name)
        else:
            set_voice(name)

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
        
    def send_text_command(self, text: str) -> None:
        text = text.strip()
        if text:
            shared_state["manual_queue"].append(text)

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
        """
        import os
        os._exit(0)

    def get_state(self) -> dict:
        speech_start = shared_state.get("speech_start_time", 0)
        speech_elapsed = time.time() - speech_start if speech_start else 0
        return {
            "state": shared_state.get("state", "idle"),
            "text": shared_state.get("text", ""),
            "theme": shared_state.get("theme", "dark"),
            "accent_color": shared_state.get("accent_color", "#22d3ee"),
            "chat_history": shared_state.get("chat_history", [])[-30:],
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
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
        webview.start()

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