import os
from deep_translator import GoogleTranslator
import qrcode


def translate_text(text: str, target_language: str = "en") -> str:
    """Translates text to a target language (e.g. 'en', 'ru', 'es', 'fr')."""
    try:
        result = GoogleTranslator(source="auto", target=target_language).translate(text)
        return result
    except Exception as e:
        return f"Couldn't translate that: {e}"


def generate_qr_code(text: str) -> str:
    """Generates a QR code image for the given text/URL and saves it to the Desktop."""
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        path = os.path.join(desktop, "qr_code.png")
        img = qrcode.make(text)
        img.save(path)
        return "QR code saved to the Desktop."
    except Exception as e:
        return f"Couldn't generate QR code: {e}"


def word_count(text: str) -> str:
    """Counts words and characters in a piece of text."""
    words = len(text.split())
    chars = len(text)
    return f"{words} words, {chars} characters."