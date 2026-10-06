"""ui_state для облака: окна интерфейса нет — состояние живёт в памяти процесса."""
shared_state = {"chat_history": [], "manual_queue": [], "state": "idle", "text": ""}


def notify(*args, **kwargs):
    pass
