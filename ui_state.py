# Общее состояние интерфейса — единая точка правды, на которую
# ссылаются main.py (голосовой цикл), gui.py (отрисовка) и
# theme_control.py (голосовая команда смены темы).
shared_state = {
    "state": "idle",           # idle / listening / thinking / speaking
    "text": "",                # текущая фраза для подписи под HUD
    "should_quit": False,
    "theme": "dark",           # dark / light
    "accent_color": "#00d9ff", # можно менять из интерфейса
    "chat_history": [],        # список (speaker, text) для панели справа
    "manual_queue": [],        # текстовые команды, введённые руками
    "always_listening": False,
    "speech_envelope": [],
    "speech_duration": 0,
    "speech_start_time": 0,
}