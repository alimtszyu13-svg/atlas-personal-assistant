"""
Сравнение двух документов: «Атлас, сравни эссе и эссе v2», «чем отличается новый договор от старого»,
«сравни две последние версии реферата» — или «Сравнить два файла» в окне «Записи».

Точные отличия считает сам Atlas (по абзацам: добавлено / убрано / изменено, с цитатами), а модель только
объясняет, что они значат по смыслу. Результат — в окне «Записи»: вкладка «Конспект» — что поменялось по
смыслу, «Полный текст» — все правки подряд.
"""
import difflib
import os
import re
import threading
import uuid


def paragraphs(text: str) -> list:
    return [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()
            and not re.fullmatch(r"\[стр\. \d+\]", p.strip())]


def word_diff(a: str, b: str) -> str:
    """«было → стало» по словам внутри абзаца: [−убрано] {+добавлено}."""
    wa, wb = a.split(), b.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if op == "equal":
            seg = wa[i1:i2]
            out.append(" ".join(seg) if len(seg) <= 8 else " ".join(seg[:3]) + " … " + " ".join(seg[-3:]))
        if op in ("delete", "replace"):
            out.append("[−" + " ".join(wa[i1:i2]) + "]")
        if op in ("insert", "replace"):
            out.append("{+" + " ".join(wb[j1:j2]) + "}")
    return " ".join(out)


def diff(text_a: str, text_b: str) -> dict:
    """→ {"added", "removed", "changed", "same", "lines": [читаемые правки], "similar": доля совпадения}."""
    pa, pb = paragraphs(text_a), paragraphs(text_b)
    sm = difflib.SequenceMatcher(None, pa, pb, autojunk=False)
    lines, added, removed, changed, same = [], 0, 0, 0, 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            same += i2 - i1
        elif op == "insert":
            added += j2 - j1
            lines += [f"+ Добавлено: {p}" for p in pb[j1:j2]]
        elif op == "delete":
            removed += i2 - i1
            lines += [f"− Убрано: {p}" for p in pa[i1:i2]]
        else:                                               # replace: похожие абзацы — по словам, остальное — целиком
            a_par, b_par = pa[i1:i2], pb[j1:j2]
            for k in range(max(len(a_par), len(b_par))):
                x = a_par[k] if k < len(a_par) else None
                y = b_par[k] if k < len(b_par) else None
                if x and y and difflib.SequenceMatcher(None, x, y).ratio() > 0.5:
                    changed += 1
                    lines.append(f"~ Изменено: {word_diff(x, y)}")
                else:
                    if x:
                        removed += 1
                        lines.append(f"− Убрано: {x}")
                    if y:
                        added += 1
                        lines.append(f"+ Добавлено: {y}")
    total = max(1, max(len(pa), len(pb)))
    return {"added": added, "removed": removed, "changed": changed, "same": same, "lines": lines,
            "similar": round(same / total, 2)}


RULES = ("You compare two versions of a document. Below are the exact changes (+ added, − removed, ~ changed: "
         "[−old] {+new}). Write in {lang}, markdown: '## Коротко' (2–4 sentences: what changed overall, which "
         "version is stronger/riskier if obvious), '## Важные изменения' (bullets — meaning, not wording; for "
         "contracts: sums, dates, obligations, penalties), '## Мелкие правки' (one line), '## На что обратить "
         "внимание'. Section titles in {lang}. Don't invent changes that aren't listed.")


def report(name_a: str, name_b: str, d: dict, ask=None, lang: str = "Russian") -> str:
    from core import quick_llm
    head = (f"Документы: «{name_a}» (было) → «{name_b}» (стало). Абзацев без изменений: {d['same']}, "
            f"изменено: {d['changed']}, добавлено: {d['added']}, убрано: {d['removed']}.")
    if not d["lines"]:
        return "## Коротко\nДокументы совпадают по тексту — отличий нет."
    body = "\n".join(d["lines"])[:45000]
    return (ask or quick_llm.ask)(RULES.replace("{lang}", lang), head + "\n\n" + body, max_tokens=1400).strip()


def compare(path_a: str, path_b: str, announce=None, ask=None, wait: bool = False, reader=None) -> str:
    """Сравнить в фоне (запись в окне «Записи»). path_a — было, path_b — стало. → id задачи."""
    from core import doc_text, doc_notes, media_import, meeting_notes as M
    na, nb = os.path.basename(path_a), os.path.basename(path_b)
    title = f"Сравнение: {os.path.splitext(na)[0]} ↔ {os.path.splitext(nb)[0]}"[:80]
    jid = uuid.uuid4().hex[:8]
    media_import._jobs[jid] = {"title": title, "pct": 5, "stage": "сравнение", "error": "", "path": path_b}
    M._changed()

    def run():
        job = media_import._jobs[jid]
        try:
            read = reader or doc_text.read
            d = diff(read(path_a)["text"], read(path_b)["text"])
            job["pct"] = 40
            lang = "English" if _lang() == "en" else "Russian"
            md = report(na, nb, d, ask=ask, lang=lang)
            stats = (f"Без изменений: {d['same']} абз. · изменено: {d['changed']} · добавлено: {d['added']} · "
                     f"убрано: {d['removed']}")
            full = f"# «{na}» → «{nb}»\n\n{stats}\n\n" + "\n\n".join(d["lines"] or ["Отличий нет."])
            doc_notes._finish(job, title, full, path_b, md, announce, kind="compare")
        except Exception as e:
            job.update(stage="ошибка", error=str(e)[:200])
            M._changed()
            if announce:
                announce(f"Не получилось сравнить: {str(e)[:140]}")
        finally:
            threading.Timer(30, lambda: media_import._jobs.pop(jid, None)
                            if media_import._jobs.get(jid, {}).get("stage") in ("готово", "ошибка") else None).start()
    t = threading.Thread(target=run, daemon=True, name=f"compare-{jid}")
    t.start()
    if wait:
        t.join()
    return jid


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


# ---------------------------------------------------------------------------
# Голосом
# ---------------------------------------------------------------------------
_TWO = re.compile(r"^(?:сравни|сопоставь|compare)\s+(?P<a>.+?)\s+(?:и|с|со|with|and)\s+(?P<b>.+)$", re.I)
_DIFF = re.compile(r"^(?:чем|в чём|в чем)\s+(?:отличается|отличаются|разница между)\s+(?P<b>.+?)\s+(?:от|и)\s+(?P<a>.+)$",
                   re.I)
_VERSIONS = re.compile(r"^(?:сравни|сопоставь|compare)\s+(?:две|2|последние две|две последние)\s+(?:последние\s+)?"
                       r"(?:версии|варианта|редакции|копии)\s+(?P<q>.+)$|^(?:сравни|compare)\s+(?:версии|versions of)\s+(?P<q2>.+)$",
                       re.I)


def intent(text: str):
    """→ ("compare", запрос_a, запрос_b) | ("versions", запрос) | None."""
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", (text or "").strip(), flags=re.I).strip(" .!?")
    m = _VERSIONS.match(t)
    if m:
        return ("versions", (m.group("q") or m.group("q2")).strip())
    m = _TWO.match(t) or _DIFF.match(t)
    if m:
        return ("compare", m.group("a").strip(), m.group("b").strip())
    return None


def ask(intent_value, announce=None, finder=None) -> str:
    from core import doc_text, media_import
    find = finder or media_import.find
    docs = lambda q: [p for p in find(q) if doc_text.is_doc(p) and not media_import.is_media(p)]
    if intent_value[0] == "versions":
        hits = sorted(docs(intent_value[1]), key=lambda p: os.path.getmtime(p))[-2:]
        if len(hits) < 2:
            return f"Нашёл меньше двух документов по «{intent_value[1]}»."
        a, b = hits
    else:
        ha, hb = docs(intent_value[1]), docs(intent_value[2])
        if not ha or not hb:
            missing = intent_value[1] if not ha else intent_value[2]
            return f"Не нашёл документ «{missing}». Открой окно «Записи» → «Сравнить два файла»."
        a = ha[0]
        b = next((p for p in hb if p != a), None)
        if not b:
            return "Оба описания указывают на один и тот же файл — назови их точнее."
        if os.path.getmtime(a) > os.path.getmtime(b) and re.search(r"стар|прошл|old|первый", intent_value[2], re.I):
            a, b = b, a
    compare(a, b, announce=announce)
    return f"Сравниваю «{os.path.basename(a)}» → «{os.path.basename(b)}». Скажу, когда будет готово."
