"""
Два бага из лога от 30.09 + облегчение запросов.

1) Бесконечный поток «[gateway] лимит минуты (13059/7600), жду 0.0с».
   Отметка «модель занята N секунд» (после отказа Groq) добавлялась в конец окна,
   хотя по времени она старше соседних записей. Окно очищалось только с начала —
   истёкшая отметка застревала навсегда, шлюз считал модель переполненной и при
   этом вычислял ожидание 0 с → крутился в цикле без пауз.
   Теперь: истёкшие записи удаляются где бы они ни стояли, записи сортируются
   по времени, и ожидание никогда не бывает меньше 0,25 с.

2) «Сохранил» — а на экране висит «HELLO THERE.txt уже существует. Заменить?».
   Агент программ смотрел только в главное окно и не видел диалогов поверх него.
   Теперь после каждого действия он ищет открытые диалоги (в том числе диалог
   поверх диалога) и переключается на них: «⚠ Появилось окно «…» — оно ждёт
   ответа». Модели прямо сказано: не считать задачу выполненной, пока свежий
   список элементов этого не подтверждает, а вопросы «заменить?» — задавать тебе.

3) Запросы легче: каждый весил ~5,5 тысяч токенов — один запрос в минуту на модель.
   Списки элементов браузера и программ короче (45 вместо 60–70), результаты
   инструментов — до 2500 символов, запрос ужимается до 4500 токенов.

4) Упёрлись в лимит — Atlas честно говорит «дайте полминуты», а не «проблема со связью».

Запуск из корня проекта:  python apply_fix_flood.py
Резервная копия: backup_flood_<время>/
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["core/llm_gateway.py", "core/desktop_agent.py", "browser_agent.py", "ai_brain.py"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas.")
backup = os.path.join(ROOT, time.strftime("backup_flood_%Y%m%d_%H%M%S"))
for f in FILES:
    os.makedirs(os.path.dirname(os.path.join(backup, f)), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, f), os.path.join(backup, f))
print(f"Резервная копия: {backup}")
src = {f: open(os.path.join(ROOT, f), encoding="utf-8").read() for f in FILES}
report = []


def rep(f, old, new, what):
    s = src[f]
    if new in s:
        report.append(f"  ✓ {f}: {what} (уже было)")
    elif s.count(old) == 1:
        src[f] = s.replace(old, new, 1)
        report.append(f"  ✓ {f}: {what}")
    else:
        report.append(f"  ! {f}: {what} — фрагмент не найден, пропущено")


# ---------------------------------------------------------------------------
# 1. core/llm_gateway.py
# ---------------------------------------------------------------------------
G = "core/llm_gateway.py"
rep(G, '''def _used(win: deque, now: float) -> int:
    while win and now - win[0][0] > 60:
        win.popleft()
    return sum(t for _, t in win)''', '''def _used(win: deque, now: float) -> int:
    """Расход за последние 60 с. Истёкшие записи удаляются, ГДЕ БЫ они ни стояли:
    отметка «занята N секунд» бывает старше соседних записей, и раньше она застревала."""
    if any(now - t > 60 for t, _ in win):
        keep = sorted((t, n) for t, n in win if now - t <= 60)
        win.clear()
        win.extend(keep)
    return sum(t for _, t in win)''', "истёкшие записи не застревают в окне")
rep(G, '''    need = used + tokens - budget
    freed = 0
    for t, n in win:''', '''    need = used + tokens - budget
    freed = 0
    for t, n in sorted(win):                    # по времени, даже если записи добавлены не по порядку''',
    "ожидание считается по записям в порядке времени")
rep(G, "            wait = _wait_for(win, now, tokens, budget)   # точно под этот запрос\n",
    "            wait = _wait_for(win, now, tokens, budget)   # точно под этот запрос\n"
    "            wait = max(wait, 0.25)                        # никогда не крутимся вхолостую\n",
    "ожидание не бывает нулевым")
rep(G, "MAX_REQUEST = 6000", "MAX_REQUEST = 4500", "запрос ужимается до 4500 токенов")

# ---------------------------------------------------------------------------
# 2. core/desktop_agent.py — диалоги поверх окна
# ---------------------------------------------------------------------------
D = "core/desktop_agent.py"
rep(D, '''def _activate(hwnd) -> None:''', '''def _owned_popups(owner) -> list:
    """Видимые окна, которыми владеет owner: диалоги «Сохранить как», «Заменить?» и т.п."""
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, _lp):
        if u.IsWindowVisible(h) and u.GetWindow(h, 4) == owner:      # GW_OWNER
            found.append(h)
        return True
    u.EnumWindows(proc(cb), 0)
    return found


def _top_dialog(hwnd):
    """Самый верхний диалог поверх окна (диалог поверх диалога тоже), или None."""
    try:
        cur, depth = hwnd, 0
        while depth < 5:
            pops = [h for h in _owned_popups(cur) if _title(h) and not _is_atlas(_title(h))]
            if not pops:
                break
            cur, depth = pops[0], depth + 1
        return cur if cur != hwnd else None
    except Exception:
        return None


def _activate(hwnd) -> None:''', "поиск диалогов поверх окна")
rep(D, '''def _refresh() -> str:
    time.sleep(0.45)
    return _snapshot(_target["hwnd"] if _target["hwnd"] else None)''', '''def _refresh() -> str:
    """После действия: не появился ли диалог или другое окно — тогда работаем в нём."""
    time.sleep(0.5)
    h, note = _target["hwnd"], ""
    if h:
        dlg = _top_dialog(h)
        if dlg:
            h = dlg
            note = f"⚠ Появилось окно «{_title(dlg)[:80]}» — оно ждёт ответа, работаю в нём.\\n"
        else:
            try:
                u = ctypes.windll.user32
                fg = u.GetForegroundWindow()
                if fg and fg != h and u.IsWindowVisible(fg) and not _is_atlas(_title(fg)):
                    h = fg
                    note = f"Теперь активно окно «{_title(fg)[:80]}».\\n"
            except Exception:
                pass
    return note + _snapshot(h)''', "после действия замечает диалоги и новые окна")
rep(D, '''        hwnd, _t = _pick_target(window)
        if hwnd and window:
            _activate(hwnd)
        return _snapshot(hwnd, window)''', '''        hwnd, _t = _pick_target(window)
        if hwnd and window:
            _activate(hwnd)
        dlg = _top_dialog(hwnd) if hwnd else None
        if dlg:                                   # поверх окна висит диалог — смотрим в него
            return f"⚠ Поверх окна открыт диалог «{_title(dlg)[:80]}».\\n" + _snapshot(dlg)
        return _snapshot(hwnd, window)''', "desktop_look видит открытый диалог")
rep(D, "MAX_ELEMENTS = 70", "MAX_ELEMENTS = 45", "список элементов программы короче")

# ---------------------------------------------------------------------------
# 3. browser_agent.py — короче список элементов
# ---------------------------------------------------------------------------
rep("browser_agent.py", "MAX_ELEMENTS = 60", "MAX_ELEMENTS = 45", "список элементов страницы короче")
rep("browser_agent.py", "READ_CHARS = 3500", "READ_CHARS = 2400", "текст страницы — частями по 2400 символов")

# ---------------------------------------------------------------------------
# 4. ai_brain.py
# ---------------------------------------------------------------------------
A = "ai_brain.py"
rep(A, "MAX_TOOL_RESULT_CHARS = 3500  #", "MAX_TOOL_RESULT_CHARS = 2500  #", "результаты инструментов до 2500 символов")
rep(A, '''        return "Не могу сейчас ответить, проблема со связью."''',
    '''        if "rate_limit" in str(e).lower() or "429" in str(e):
            from voice import get_response_language
            return ("Упёрся в минутный лимит запросов — дайте мне полминуты, сэр."
                    if get_response_language() == "ru"
                    else "I've hit the per-minute request limit — give me half a minute, sir.")
        return "Не могу сейчас ответить, проблема со связью."''', "честное сообщение о лимите")
RULE = '''

# === Правило: задача выполнена только когда это видно ===
_DONE_RULE = (" VERIFY BEFORE CLAIMING: never say a desktop or browser task is done until the fresh state returned "
              "by your last action confirms it (saved file name in the window title, no dialog left open, the page "
              "shows the result). If a dialog asks to replace, overwrite, delete or send, ask the user first.")
if "VERIFY BEFORE CLAIMING" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _DONE_RULE
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT
'''
if "# === Правило: задача выполнена только когда это видно ===" in src[A]:
    report.append(f"  ✓ {A}: правило проверки результата (уже было)")
else:
    src[A] = src[A].rstrip() + "\n" + RULE
    report.append(f"  ✓ {A}: правило «не считать выполненным, пока не видно»")

ok = True
for f in FILES:
    try:
        ast.parse(src[f], filename=f)
    except SyntaxError as e:
        ok = False
        report.append(f"  ! {f}: {e} — файл НЕ изменён")
        continue
    open(os.path.join(ROOT, f), "w", encoding="utf-8", newline="\n").write(src[f])
    report.append(f"  ✓ синтаксис {f}")
print("\n".join(report))
print("\nГотово. Запускай: python main.py" if ok else f"\nЕсть ошибка — пришли вывод (копия: {backup}).")
