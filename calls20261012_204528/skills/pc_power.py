"""
Джарвис для ПК: окна голосом, «скажи, когда…», уборка диска, текст с экрана (Ctrl+Alt+X).
Всё работает только на этом компьютере (core/window_layout, watchers, disk_clean, screen_snip).
"""
import re

from core import disk_clean, messengers, screen_snip, watchers, window_layout
from core.skills import skill

try:
    import tool_router
    tool_router.TRIGGERS["pc_power"] = (
        "слева", "справа", "налево", "направо", "на весь экран", "разверни", "сверни", "монитор", "раздели экран",
        "окна", "window", "monitor", "maximize", "minimize", "side by side",
        "скажи, когда", "скажи когда", "сообщи, когда", "сообщи когда", "дай знать", "когда скачается",
        "когда закончится", "когда закроется", "следи", "следишь", "tell me when", "let me know when", "notify me",
        "занимает место", "места на диске", "почисти", "очисти компьютер", "освободи место", "мусор", "кэш",
        "disk space", "clean up", "free up",
        "телеграм", "ватсап", "вотсап", "инст", "дискорд", "telegram", "whatsapp", "instagram", "discord",
        "ответь", "напиши ему", "напиши ей", "что написал", "что пишут", "reply to", "text ",
        "сожми", "zip", "архив", "распакуй", "mp3", "pdf", "конвертир", "переименуй", "где я сохранил", "куда я",
        "скачал", "под курсором", "режим", "сценари",
        "с экрана", "с картинки", "что выделю", "распознай", "скриншот", "снимок", "ocr", "from the screen",
    )
except Exception:
    pass


def _announce(text: str) -> None:
    try:
        from brain import state
        f = state.announce.get("fn")
        if f:
            f(text)
    except Exception:
        pass


@skill("pc_power",
       description="Moves, snaps, maximizes or minimizes app windows on this PC, across monitors. layout examples: "
                   "'chrome:left; word:right', 'telegram:max', 'code:tr', 'chrome:left monitor 2', 'tile: chrome, code', "
                   "'minimize all except: code', 'undo'. Positions: left right top bottom tl tr bl br l3 c3 r3 center "
                   "max min. App names like chrome, word, excel, code, telegram, discord, explorer, spotify, browser.",
       params={"layout": "what goes where, e.g. 'chrome:left; word:right'"})
def arrange_windows(layout: str) -> str:
    return window_layout.arrange(layout)


@skill("pc_power",
       description="Watches the PC and tells the user when something happens: a download finishes, a program "
                   "finishes rendering/installing/exporting (CPU goes quiet) or closes, a web page shows some text "
                   "or changes, a new file appears in a folder, the internet comes back, the battery charges. "
                   "what: the user's own words after 'когда', e.g. 'скачается файл', 'закончится рендер в blender', "
                   "'на этой странице появится «в наличии»', 'вернётся интернет'.",
       params={"what": "what to wait for, in the user's words"})
def watch_for(what: str) -> str:
    w = watchers.parse(what, what)
    if not w:
        return ("Can watch: downloads, a program finishing or closing, a web page text/change, new files in a "
                "folder, internet, battery. Ask the user which.")
    return watchers.add(w)


@skill("pc_power", read_only=True, description="Lists what Atlas is currently watching for ('tell me when…').")
def list_watches() -> str:
    return watchers.listing(ru=False)


@skill("pc_power", description="Stops watching: one ('загрузка', a number) or all (empty).",
       params={"which": "words about the watch, its number, or empty for all"})
def cancel_watch(which: str = "") -> str:
    return watchers.cancel(which, ru=False)


@skill("pc_power", read_only=True,
       description="What takes up disk space on this PC: free space per drive, biggest user folders, largest files, "
                   "how much junk (temp files, browser cache, Recycle Bin) can be cleaned.")
def disk_usage_report() -> str:
    return disk_clean.describe_usage(disk_clean.usage(), ru=False)


@skill("pc_power",
       description="Prepares a disk clean-up plan (temp files, browser cache, old installers in Downloads → Recycle "
                   "Bin; Recycle Bin itself only if asked) and says how much space it frees. Nothing is deleted until "
                   "the user agrees — then call confirm_disk_cleanup.",
       params={"empty_recycle_bin": "true only if the user asked to empty the Recycle Bin too"})
def plan_disk_cleanup(empty_recycle_bin: bool = False) -> str:
    said = disk_clean.propose("clean recycle bin" if empty_recycle_bin else "clean")
    disk_clean._s["said"] = said
    return said


@skill("pc_power", description="Runs the clean-up plan the user just agreed to (after plan_disk_cleanup and a 'yes').")
def confirm_disk_cleanup() -> str:
    return disk_clean.confirm("yes", announce=_announce)


@skill("pc_power",
       description="Lets the user select a screen region with the mouse, recognizes the text in it (OCR) and copies it "
                   "to the clipboard. action: 'copy' (default), 'explain' (explain what it says / the error), "
                   "'translate' (Russian↔English). Works on images, videos, scans, games.",
       params={"action": "copy | explain | translate"})
def snip_screen_text(action: str = "copy") -> str:
    action = action if action in ("copy", "explain", "translate") else "copy"
    short, full = screen_snip.snip(action, ru=False)
    return full or short


@skill("pc_power",
       description="Writes a chat message for the user in Telegram, WhatsApp, Instagram or Discord on this PC: opens "
                   "the chat, reads the visible conversation, drafts the message in the user's style and ASKS the user "
                   "to confirm (it is never sent without 'yes'; then call confirm_message_send). app: telegram | "
                   "whatsapp | instagram | discord | current (the chat open on screen). contact: name as the user said "
                   "it. message: what the user wants to say; exact=true if they dictated the exact text.",
       params={"app": "telegram | whatsapp | instagram | discord | current", "contact": "who, in the user's words",
               "message": "what to say (gist) or the exact text", "exact": "true if the text is dictated word for word"})
def send_message(app: str, contact: str = "", message: str = "", exact: bool = False) -> str:
    return messengers.tool_prepare(app, contact, message, bool(exact))


@skill("pc_power", description="Sends the drafted messenger message after the user said yes (after send_message).")
def confirm_message_send() -> str:
    return messengers.send()


@skill("pc_power", read_only=True,
       description="Reads what someone wrote in Telegram/WhatsApp/Instagram/Discord on this PC and summarizes it. "
                   "contact empty — who wrote recently (chat list).",
       params={"app": "telegram | whatsapp | instagram | discord", "contact": "whose chat, or empty"})
def read_messages(app: str, contact: str = "") -> str:
    key = next((k for k, v in messengers.APPS.items() if (app or "").lower() == k or
                re.search(v["rx"], app or "", re.I)), None)
    if not key:
        return "Supported: Telegram, WhatsApp, Instagram, Discord."
    ru = messengers._ru(contact)
    return messengers.read(key, contact, ru) if contact else messengers.overview(key, ru)


# --- пакет 1: файлы, «где сохранил», «что это?», сценарии ---------------------------------------
@skill("pc_power",
       description="Batch file work on this PC; originals are never changed (new files are written next to them). "
                   "action: compress_images | compress_video | to_mp3 | convert_images | images_to_pdf | merge_pdf | "
                   "office_to_pdf | zip | unzip | rename_by_date | undo_rename. where: selected (files selected in "
                   "Explorer) | this folder | downloads | desktop | documents | pictures | videos. fmt: for "
                   "convert_images — jpg | png | webp.",
       params={"action": "what to do", "where": "which files", "fmt": "target image format for convert_images"})
def batch_files(action: str, where: str = "selected", fmt: str = "") -> str:
    from core import file_ops
    return file_ops.tool(action, where, fmt)


@skill("pc_power", read_only=True,
       description="Finds where the user saved or downloaded a file recently (Downloads, Desktop, Documents, Windows "
                   "recent files), by words from its name and time words ('вчера', 'утром'). Returns name, folder, time.",
       params={"query": "the user's words, e.g. 'реферат, который я вчера скачал'"})
def find_saved_file(query: str) -> str:
    from core import where_saved
    hits = where_saved.find(query)
    if not hits:
        return "Nothing like that among recent files."
    import time as _t
    return "; ".join(f"{h['path']} ({_t.strftime('%d.%m %H:%M', _t.localtime(h['time']))})" for h in hits[:3])


@skill("pc_power", read_only=True,
       description="Explains what is under the mouse cursor right now (button, icon, word, error, chart).")
def explain_under_cursor() -> str:
    from core import what_is
    return what_is.explain(what_is.look(), ru=False)


@skill("pc_power",
       description="Saves a voice scenario: several Atlas commands under one name, run later by saying "
                   "'режим <name>'. steps: the commands as the user said them, separated by ';'.",
       params={"name": "e.g. 'кино'", "steps": "e.g. 'сверни всё; яркость 40; громкость 60; открой кинопоиск'"})
def save_scenario(name: str, steps: str) -> str:
    from core import scenarios
    return scenarios.create(name, scenarios.split_steps(steps), ru=scenarios._ru(name + steps))


@skill("pc_power", description="Runs a saved scenario by name ('кино', 'учёба').", params={"name": "scenario name"})
def run_scenario(name: str) -> str:
    from core import pc_fast, scenarios
    return scenarios.run(scenarios._key(name), pc_fast.default_runner, ru=scenarios._ru(name))
