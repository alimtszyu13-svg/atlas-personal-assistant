"""
Слова управления: «стоп» и «выключись».

Раньше одна и та же проверка была написана в main.py дважды (для голоса и для текста в чате).
Теперь она в одном месте и покрыта тестами.
"""
import re

STOP_WORDS = {"stop", "стоп", "хватит", "cancel", "отмена", "enough", "стой", "прекрати", "перестань",
              "остановись", "довольно", "не надо", "отбой", "тихо", "замолчи", "wait", "pause"}

_SHUTDOWN_RE = re.compile(
    r"^(?:выключ\w*|отключ\w*|выключи себя|заверши работу|завершить работу|закончи работу|закройся|иди спать|"
    r"shut\s?down|turn off|power off|turn yourself off|go to sleep|exit|quit)$")


def normalize(text: str) -> str:
    """«Атлас, выключись, пожалуйста!» → «выключись»."""
    cmd = (text or "").lower().strip(" .!?,")
    cmd = re.sub(r"^(?:atlas|атлас)[,\s]+", "", cmd)
    return re.sub(r"[,\s]+(?:please|пожалуйста)$", "", cmd).strip()


def is_stop(text: str) -> bool:
    """«Стоп», «хватит», «отбой» — прервать/ничего не делать (без запроса к модели)."""
    return normalize(text) in STOP_WORDS


def is_shutdown(text: str) -> bool:
    """«Выключись», «заверши работу», «go to sleep» — завершить Atlas."""
    return bool(_SHUTDOWN_RE.match(normalize(text)))
