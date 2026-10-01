"""
Быстрее + компактные субтитры.

Скорость (по логу 01.10):
  1) Gemini думал по 3–11 секунд на шаг. Теперь размышления выключены
     (если модель не позволяет — минимальные), ответ за 1–2 секунды.
  2) Gemini брался, даже когда Groq освобождался через 1–2 секунды, хотя Groq
     отвечает за полсекунды. Теперь у Gemini «цена» 3 секунды: он берётся,
     только если ждать Groq дольше.
  3) Лишний шаг remember_fact посреди поиска («wants_info → Pushkin death») — ещё
     3–7 секунд на каждый вопрос. Модели сказано: запоминать только факты о твоей
     жизни, а не то, что ты попросил найти. Ночная «уборка» памяти тоже больше
     не записывает такие факты.

Субтитры: компактная «стеклянная» плашка под стиль интерфейса — твоя фраза
мелкой строкой, ответ Atlas — не больше двух строк, ширина по тексту (до 620 px),
пустая плашка не видна.

Запуск из корня проекта:  python apply_speed_captions.py
Резервная копия: backup_speedcap_<время>/
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["ai_brain.py", "core/llm_gateway.py", "core/memory.py", "atlas_ui.html"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas.")
backup = os.path.join(ROOT, time.strftime("backup_speedcap_%Y%m%d_%H%M%S"))
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
# core/llm_gateway.py — «цена» медленной модели
# ---------------------------------------------------------------------------
G = "core/llm_gateway.py"
rep(G, "_lock = threading.Lock()\n",
    "MODEL_PENALTY = {}   # модель → секунды «цены»: медленную берём, только если быструю ждать дольше\n"
    "_lock = threading.Lock()\n", "у моделей может быть «цена» в секундах")
rep(G, '''    best = None
    with _lock:
        now = time.time()
        for m in models:''', '''    best, best_eff = None, 0.0
    with _lock:
        now = time.time()
        for m in models:''', "выбор модели с учётом «цены»: подготовка")
rep(G, '''            if best is None or wait < best[1] - 0.05:
                best = (m, wait, room)''', '''            eff = wait + MODEL_PENALTY.get(m, 0.0)
            if best is None or eff < best_eff - 0.05:
                best, best_eff = (m, wait, room), eff''', "выбор модели с учётом «цены»")

# ---------------------------------------------------------------------------
# ai_brain.py — Gemini без размышлений и с «ценой»; правило remember_fact
# ---------------------------------------------------------------------------
A = "ai_brain.py"
if "GEM_MODEL" in src[A]:
    rep(A, '_GEM_DUMMY_SIG = {"google": {"thought_signature": "skip_thought_signature_validator"}}\n',
        '_GEM_EFFORT = {"v": os.getenv("GEMINI_EFFORT") or "none"}   # без размышлений — быстро\n'
        '_GEM_DUMMY_SIG = {"google": {"thought_signature": "skip_thought_signature_validator"}}\n',
        "Gemini: уровень размышлений")
    rep(A, '        kw["reasoning_effort"] = "low"            # быстрее и дешевле по лимиту\n',
        '        kw["reasoning_effort"] = _GEM_EFFORT["v"]  # без размышлений: 1–2 с вместо 3–11 с\n',
        "Gemini отвечает без долгих размышлений")
    rep(A, '''    r = cl.chat.completions.create(**kw)
    ch = r.choices[0] if getattr(r, "choices", None) else None
    if ch is None:
        raise RuntimeError(f"ответ без choices: {str(r)[:200]}")''', '''    for _try in range(3):
        try:
            r = cl.chat.completions.create(**kw)
            break
        except Exception as e:
            bad = str(e).lower()
            nxt = {"none": "minimal", "minimal": "low"}.get(kw.get("reasoning_effort"))
            if nxt and ("reasoning" in bad or "thinking" in bad or "400" in bad):
                print(f"[gemini] уровень размышлений «{kw['reasoning_effort']}» не принят — пробую «{nxt}»")
                _GEM_EFFORT["v"] = kw["reasoning_effort"] = nxt
                continue
            raise
    ch = r.choices[0] if getattr(r, "choices", None) else None
    if ch is None:
        raise RuntimeError(f"ответ без choices: {str(r)[:200]}")''', "если «без размышлений» нельзя — минимальные")
    rep(A, "    llm_gateway.MODEL_TPM[GEM_MODEL] = 250000\n",
        "    llm_gateway.MODEL_TPM[GEM_MODEL] = 250000\n"
        "    llm_gateway.MODEL_PENALTY[GEM_MODEL] = float(os.getenv(\"GEMINI_PENALTY\") or 3.0)\n",
        "Gemini берётся, только если Groq ждать дольше 3 с")
else:
    report.append(f"  · {A}: Gemini не подключён — его настройки пропущены")
RULE = '''

# === Правило: запоминать только факты о жизни пользователя ===
_MEM_RULE = (" REMEMBER_FACT is ONLY for facts about the user's own life that they tell you (their goals, dates, "
             "preferences, people, projects). Never call it for what the user asks you to look up, search, explain, "
             "watch or listen to, and never as a separate step in the middle of a search — it costs the user time.")
if "REMEMBER_FACT is ONLY" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _MEM_RULE
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT
'''
if "# === Правило: запоминать только факты о жизни пользователя ===" in src[A]:
    report.append(f"  ✓ {A}: правило для remember_fact (уже было)")
else:
    src[A] = src[A].rstrip() + "\n" + RULE
    report.append(f"  ✓ {A}: remember_fact — только факты о твоей жизни")

# ---------------------------------------------------------------------------
# core/memory.py — ночная «уборка» не пишет, что ты искал
# ---------------------------------------------------------------------------
rep("core/memory.py", '''"false for lists (likes). Don't invent facts; skip anything uncertain."''',
    '''"false for lists (likes). Don't invent facts; skip anything uncertain. Never store what the user "
    "merely asked about, searched for, or wanted to watch or listen to — only facts about the user's own life."''',
    "память не хранит, что ты просто искал")

# ---------------------------------------------------------------------------
# atlas_ui.html — компактные субтитры
# ---------------------------------------------------------------------------
U = "atlas_ui.html"
CSS = """  /* ===================== Компактные субтитры ===================== */
  .captions { bottom: calc(26px + env(safe-area-inset-bottom, 0px)); width: fit-content !important;
    max-width: min(620px, calc(100% - 64px)); gap: 3px; padding: 8px 16px 9px; border-radius: 14px;
    background: rgba(6,10,22,.55); border: 1px solid rgba(134,214,255,.16);
    -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
    box-shadow: 0 8px 30px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.04);
    transition: opacity .35s ease, left .35s ease; }
  .captions:not(:has(.you:not(:empty))):not(:has(.atlas:not(:empty))) { opacity: 0; pointer-events: none; }
  .captions .you { font-size: 12px; min-height: 0; color: var(--muted); letter-spacing: .02em; max-width: 100%;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .captions .you:not(:empty)::before { content: "› "; color: var(--ion); }
  .captions .atlas { font-size: 15px; line-height: 1.45; font-weight: 500; min-height: 0; text-shadow: none;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .captions .you:empty, .captions .atlas:empty { display: none; }
  body[data-theme="light"] .captions { background: rgba(255,255,255,.72); border-color: rgba(30,60,120,.14);
    box-shadow: 0 8px 26px rgba(30,60,120,.12); }
</style>"""
if "Компактные субтитры" in src[U]:
    report.append(f"  ✓ {U}: компактные субтитры (уже было)")
elif src[U].count("</style>") == 1:
    src[U] = src[U].replace("</style>", CSS, 1)
    report.append(f"  ✓ {U}: компактные субтитры")
else:
    report.append(f"  ! {U}: не нашёл, куда добавить стили")

ok = True
for f in FILES:
    if f.endswith(".py"):
        try:
            ast.parse(src[f], filename=f)
        except SyntaxError as e:
            ok = False
            report.append(f"  ! {f}: {e} — файл НЕ изменён")
            continue
    open(os.path.join(ROOT, f), "w", encoding="utf-8", newline="\n").write(src[f])
    if f.endswith(".py"):
        report.append(f"  ✓ синтаксис {f}")
print("\n".join(report))
print("\nГотово. Запускай: python main.py" if ok else f"\nЕсть ошибка — пришли вывод (копия: {backup}).")
