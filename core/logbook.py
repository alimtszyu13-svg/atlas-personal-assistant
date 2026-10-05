"""
Журнал Atlas в файлы: всё, что печатается в консоль, дописывается в logs/atlas.log.

Новый файл каждый день, хранятся последние 7 (atlas.log.2026-10-05 и т.д.) — можно прислать
лог после обычного дня, а не только после тестов. Консоль работает как раньше.
"""
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_lock = threading.Lock()


class _Tee:
    """Поток вывода, который пишет и в консоль, и в журнал (построчно, с временем)."""

    def __init__(self, original, handler, tag=""):
        self._orig, self._h, self._tag, self._buf = original, handler, tag, ""

    def write(self, s):
        try:
            self._orig.write(s)
        except Exception:
            pass
        with _lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    rec = logging.LogRecord("atlas", logging.INFO, "", 0,
                                            f"{datetime.now():%H:%M:%S} {self._tag}{line}", None, None)
                    try:
                        self._h.emit(rec)
                    except Exception:
                        pass
        return len(s)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass
        try:
            self._h.flush()
        except Exception:
            pass

    def __getattr__(self, name):            # isatty, encoding, reconfigure… — как у настоящей консоли
        return getattr(self._orig, name)


def start(days: int = 7, log_dir: str = None) -> str:
    """Подключить журнал. Повторный вызов ничего не делает. Возвращает путь к файлу."""
    if isinstance(sys.stdout, _Tee):
        return sys.stdout._h.baseFilename
    d = log_dir or _LOG_DIR
    os.makedirs(d, exist_ok=True)
    h = logging.handlers.TimedRotatingFileHandler(os.path.join(d, "atlas.log"), when="midnight",
                                                  backupCount=days, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    sys.stdout = _Tee(sys.stdout, h)
    sys.stderr = _Tee(sys.stderr, h, tag="[stderr] ")
    return h.baseFilename
