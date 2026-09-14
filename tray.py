import pystray
from PIL import Image, ImageDraw
import threading


def _create_icon_image():
    """
    Рисует простую иконку программно — синий круг на прозрачном фоне,
    в духе того же HUD-стиля. Не нужен отдельный файл картинки.
    """
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), outline=(0, 217, 255, 255), width=4)
    draw.ellipse((20, 20, size - 20, size - 20), fill=(0, 217, 255, 255))
    return image


class AtlasTray:
    def __init__(self, on_show, on_quit):
        """
        on_show — функция, вызываемая при клике "Show" (вернуть окно из трея)
        on_quit — функция, вызываемая при клике "Quit" (полностью закрыть программу)
        """
        self.on_show = on_show
        self.on_quit = on_quit
        self.icon = pystray.Icon(
            "Atlas",
            _create_icon_image(),
            "Atlas Assistant",
            menu=pystray.Menu(
                pystray.MenuItem("Show", self._show),
                pystray.MenuItem("Quit", self._quit)
            )
        )

    def _show(self, icon, item):
        self.on_show()

    def _quit(self, icon, item):
        self.icon.stop()
        self.on_quit()

    def run(self):
        """
        pystray тоже требует свой собственный поток для обработки иконки —
        запускаем в фоне, отдельно и от голоса, и от tkinter.
        """
        thread = threading.Thread(target=self.icon.run, daemon=True)
        thread.start()