"""
«Атлас, установи себя на телефон» (или на планшет, телевизор).

Ни Android, ни iPhone не дают программе с компьютера ставить что-то на телефон без человека —
это защита от вирусов. Поэтому от тебя одно действие: навести камеру на QR-код и нажать
«Установить». В QR-коде — защищённый адрес Atlas и секретный ключ сопряжения.

Страница с QR-кодом открывается файлом на этом компьютере и по сети не отдаётся.
"""
import base64
import html
import io
import os
import tempfile
import webbrowser

from phone import server

DEVICES = {
    "phone": ("телефон", "phone"), "tablet": ("планшет", "tablet"), "tv": ("телевизор", "TV"),
}
_ALIASES = {"телефон": "phone", "смартфон": "phone", "мобильник": "phone", "phone": "phone", "планшет": "tablet",
            "tablet": "tablet", "ipad": "tablet", "телевизор": "tv", "тв": "tv", "tv": "tv", "телек": "tv"}


def _qr_png(text: str) -> str:
    """QR-код картинкой (base64) — или пусто, если нет библиотеки qrcode (тогда на странице будет ссылка)."""
    try:
        import qrcode
        img = qrcode.make(text, box_size=9, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _page(link: str, kind: str, device: str) -> str:
    qr = _qr_png(link)
    name = DEVICES[device][0]
    tv = device == "tv"
    note = {"tailscale": "Телефон должен быть в твоей сети Tailscale: приложение Tailscale установлено и включено.",
            "cloudflare": "Адрес временный: после перезапуска Atlas скажи «установи себя на телефон» ещё раз и "
                          "отсканируй новый код."}.get(kind, "")
    steps = ("<li>Открой браузер на телевизоре и введи адрес ниже.</li><li>Разреши микрофон, если телевизор его спросит.</li>"
             if tv else
             "<li><b>Android:</b> наведи камеру → открой ссылку → нажми «Установить на телефон».</li>"
             "<li><b>iPhone:</b> наведи камеру → откроется Safari → «Поделиться» → «На экран „Домой“».</li>"
             "<li>При первом разговоре разреши доступ к микрофону.</li>")
    qr_html = (f'<img alt="QR-код для установки Atlas" src="data:image/png;base64,{qr}">' if qr and not tv else "")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Atlas на {html.escape(name)}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(120% 80% at 50% 30%,#141b3d,#050814 70%);
color:#e1eeff;font:400 17px/1.55 "Golos Text",system-ui,"Segoe UI",sans-serif}}
main{{max-width:560px;padding:40px 28px;text-align:center}}
h1{{font-weight:600;font-size:30px;margin:0 0 6px}} p{{margin:0 0 22px;color:#9aa6cf}}
img{{width:300px;height:300px;background:#fff;padding:14px;border-radius:18px;box-shadow:0 0 60px rgba(79,125,255,.45)}}
ol{{text-align:left;margin:26px auto 0;max-width:470px;padding-left:22px}} li{{margin:0 0 10px}}
.url{{word-break:break-all;font-size:{"26" if tv else "15"}px;color:#e1eeff;background:#0d1330;border:1px solid #263063;
border-radius:12px;padding:12px 14px;margin-top:18px}}
.note{{margin-top:18px;font-size:15px;color:#ffc46b}}
</style></head><body><main>
<h1>Atlas на {html.escape(name)}</h1>
<p>{"Открой этот адрес в браузере телевизора." if tv else "Наведи камеру телефона на код."}</p>
{qr_html}
{'' if qr and not tv else f'<div class="url">{html.escape(link)}</div>'}
<ol>{steps}</ol>
{f'<div class="note">{html.escape(note)}</div>' if note else ''}
</main></body></html>"""


_INSTALL_HELP = ("Чтобы телефон находил компьютер и дома, и вне дома, нужен защищённый адрес. Вариант 1, рекомендую: "
                 "Tailscale — установи на компьютер (winget install Tailscale.Tailscale) и на телефон из магазина приложений, "
                 "войди в один аккаунт, в админке Tailscale включи MagicDNS и HTTPS. Вариант 2, быстрее: Cloudflare — "
                 "winget install Cloudflare.cloudflared; на телефон ничего ставить не нужно, но адрес меняется при каждом "
                 "запуске Atlas. Потом скажи «установи себя на телефон» ещё раз.")


def install(device: str = "phone") -> str:
    """Открыть на экране QR-код установки Atlas на телефон / планшет / телевизор. → что сказать вслух."""
    d = _ALIASES.get((device or "phone").strip().lower(), "phone")
    url = server.connect()
    if not url:
        hint = server._state.get("hint") or ""
        return f"Не могу сделать защищённый адрес ({hint}). {_INSTALL_HELP}"
    token = server.pairing_token(create=True)
    link = f"{url}/?pair={token}"
    path = os.path.join(tempfile.gettempdir(), "atlas_install.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_page(link, server._state.get("kind") or "", d))
    webbrowser.open("file:///" + path.replace("\\", "/"))
    name = DEVICES[d][0]
    if d == "tv":
        return f"Открыл на экране адрес для телевизора — введи его в браузере телевизора."
    return f"Открыл на экране QR-код. Наведи на него камеру — и установи Atlas на {name}."
