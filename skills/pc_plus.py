"""
Возможности компьютера: диктовка, история буфера, фокус, конспекты созвонов и видео, напоминания по ситуации.
Данные — только на этом компьютере (core/dictation, clipboard_history, focus, meeting_notes, context_reminders).
"""
from core import clipboard_history, context_reminders, dictation, focus, meeting_notes
from core.skills import skill

try:
    import tool_router
    tool_router.TRIGGERS["pc_plus"] = (
        "пиши за мной", "диктовк", "печатай", "под диктовку", "dictat", "type what i say",
        "копировал", "скопировал", "буфер", "clipboard", "copied", "вставь ссылку", "вставь то",
        "фокус", "сосредоточ", "не отвлека", "не дай мне отвлек", "помодоро", "pomodoro", "focus", "отвлека",
        "запиши созвон", "запиши встречу", "запиши лекци", "запиши видео", "запиши звук", "конспект", "созвон",
        "закончи запись", "останови запись", "стоп запись", "что сказали", "на созвоне", "в видео сказали",
        "record the call", "record the meeting", "meeting notes", "stop recording", "transcript",
        "когда открою", "когда зайду", "когда запущу", "когда сяду за", "when i open", "when i sit",
        "напомни, когда", "напомни когда",
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


# --- диктовка ---------------------------------------------------------------
@skill("pc_plus",
       description="Starts dictation: everything the user says next is typed into the window where the cursor is "
                   "(Word, Telegram, browser), until they say 'хватит'. Use for 'пиши за мной', 'type what I say'.")
def start_dictation() -> str:
    return dictation.start()


# --- буфер обмена -----------------------------------------------------------
@skill("pc_plus", read_only=True,
       description="Searches what the user copied on this computer (clipboard history, last 30 days, no passwords): "
                   "'what did I copy an hour ago', 'the link I copied this morning', 'that phone number from "
                   "yesterday'. query: words about it (may include 'ссылка', 'номер', 'почта'); period: time words.",
       params={"query": "words about the copied text, or empty", "period": "e.g. 'час назад', 'утром', 'вчера'"})
def clipboard_history_search(query: str = "", period: str = "") -> str:
    return clipboard_history.describe(clipboard_history.search(query, period))


@skill("pc_plus",
       description="Puts something from the clipboard history back and pastes it where the cursor is: 'вставь "
                   "ссылку, которую я копировал утром'. number: which result (1 = newest match). paste=false — only "
                   "copy to the clipboard.",
       params={"query": "words about the copied text", "period": "time words, optional",
               "number": "1-based result number", "paste": "true to paste right away"})
def paste_from_clipboard_history(query: str = "", period: str = "", number: int = 1, paste: bool = True) -> str:
    items = clipboard_history.search(query, period, limit=max(1, int(number or 1)))
    if len(items) < max(1, int(number or 1)):
        return "Nothing like that in the clipboard history."
    it = items[int(number or 1) - 1]
    how = clipboard_history.put_back(it, paste=bool(paste))
    return f"'{it['text'][:120]}' — {how}."


@skill("pc_plus", description="Deletes the clipboard history (all, or for a period like 'сегодня', 'час назад').",
       params={"period": "empty or 'all' — everything; or time words"})
def forget_clipboard_history(period: str = "") -> str:
    return f"Deleted {clipboard_history.forget(period)} clipboard entries."


# --- фокус -------------------------------------------------------------------
@skill("pc_plus",
       description="Starts focus mode for N minutes: distracting apps (Discord, games, messengers) get minimized and "
                   "distracting sites (YouTube, TikTok, Instagram, VK…) closed while on screen; Atlas stays quiet; "
                   "summary at the end. allow: what stays allowed, from the user's words ('телеграм можно').",
       params={"minutes": "duration, default 50", "allow": "allowed apps/sites in the user's words, or empty"})
def start_focus(minutes: int = 50, allow: str = "") -> str:
    return focus.start(minutes, allow, announce=_announce)


@skill("pc_plus", description="Stops focus mode early and gives the summary.")
def stop_focus() -> str:
    return focus.stop()


@skill("pc_plus", read_only=True, description="How much focus time is left and how it's going.")
def focus_status() -> str:
    return focus.status()


# --- конспекты --------------------------------------------------------------
@skill("pc_plus",
       description="Starts recording the computer's sound (a call in Zoom/Meet/Discord, a lecture, a YouTube video) "
                   "to make notes later. with_mic: true for calls (the user's own voice too), false for videos/lectures.",
       params={"title": "short name, e.g. 'Созвон с командой', 'Лекция по физике'", "with_mic": "true for calls"})
def start_recording(title: str = "", with_mic: bool = True) -> str:
    return meeting_notes.start_recording(title, bool(with_mic), announce=_announce)


@skill("pc_plus",
       description="Stops the recording and makes notes: summary, decisions, tasks with deadlines, open questions "
                   "(saved to Documents/Atlas/Конспекты).")
def stop_recording() -> str:
    return meeting_notes.stop_recording()


@skill("pc_plus", read_only=True,
       description="Answers a question about a recorded call/lecture/video from its transcript: 'что сказали про "
                   "дедлайн?'. which: words from the recording's title, or empty for the last one.",
       params={"question": "the user's question", "which": "recording title words, optional"})
def ask_about_recording(question: str, which: str = "") -> str:
    return meeting_notes.ask_recording(question, which)


@skill("pc_plus", read_only=True, description="Is a recording running; the list of recent recordings.")
def recordings_status() -> str:
    return meeting_notes.status()


@skill("pc_plus", description="Sends the notes of a recording (the last one by default) to the user's phone.",
       params={"which": "recording title words, optional"})
def send_notes_to_phone(which: str = "") -> str:
    path = meeting_notes.notes_file(which)
    if not path:
        return "There are no recordings yet."
    from core import file_drop
    return file_drop.send(path)


# --- напоминания по ситуации -------------------------------------------------
@skill("pc_plus",
       description="Reminder that fires on a situation, not a time: when a program or site opens ('когда открою "
                   "Word', 'когда зайду в телеграм') or when the user comes back to the computer ('когда сяду за "
                   "компьютер'). For time-based reminders use set_reminder instead.",
       params={"text": "what to remind about", "when": "the situation in the user's words, e.g. 'когда открою Word'"})
def remind_when(text: str, when: str) -> str:
    return context_reminders.add(text, when)


@skill("pc_plus", read_only=True, description="Lists situation-based reminders (on opening a program, on coming back).")
def list_context_reminders() -> str:
    return context_reminders.listing()


@skill("pc_plus", description="Cancels situation-based reminders matching the words.",
       params={"query": "words from the reminder"})
def cancel_context_reminder(query: str) -> str:
    return context_reminders.cancel(query)
