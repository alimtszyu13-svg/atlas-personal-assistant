"""
Обновление Atlas с GitHub — безопасно: тесты до и откат, если что-то сломалось.

    «обнови себя» / python -m core.updater apply  — скачать новое с GitHub (git fetch + fast-forward),
                                                   прогнать все тесты; не прошли — вернуть как было
    «есть обновления?» / python -m core.updater check
    «откати обновление» / python -m core.updater rollback — вернуть версию до последнего обновления

Раз в день Atlas сам проверяет GitHub и один раз говорит, если есть новое (на втором компьютере удобно).
Твои незакоммиченные изменения не трогаются: если они есть — обновление не начнётся.
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "atlas_update_state.json")
BRANCH = os.getenv("ATLAS_UPDATE_BRANCH") or "main"
_s = {"run": None}


def _git(*args, timeout: float = 60) -> tuple:
    run = _s["run"] or subprocess.run
    r = run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _load() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def check() -> dict:
    """→ {"ok", "behind", "commits": [...], "dirty": bool, "error"}"""
    code, _out, err = _git("fetch", "--quiet", "origin", BRANCH, timeout=60)
    if code != 0:
        return {"ok": False, "error": err[:200] or "git fetch не удался", "behind": 0, "commits": []}
    _c, cnt, _e = _git("rev-list", "--count", f"HEAD..origin/{BRANCH}")
    _c, log, _e = _git("log", "--oneline", "--no-decorate", f"HEAD..origin/{BRANCH}")
    _c, dirty, _e = _git("status", "--porcelain", "--untracked-files=no")
    return {"ok": True, "behind": int(cnt or 0), "commits": [ln for ln in log.splitlines() if ln][:20],
            "dirty": bool(dirty.strip()), "error": ""}


def _tests() -> tuple:
    run = _s["run"] or subprocess.run
    env = dict(os.environ, ATLAS_TESTING="1")
    r = run([sys.executable, os.path.join("tests", "run_all.py")], cwd=ROOT, capture_output=True, text=True,
            timeout=1200, env=env, encoding="utf-8", errors="replace")
    tail = "\n".join((r.stdout or "").strip().splitlines()[-3:])
    return r.returncode == 0, tail


def apply(ru: bool = True) -> str:
    st = check()
    if not st["ok"]:
        return f"Не смог связаться с GitHub: {st['error']}" if ru else f"Couldn't reach GitHub: {st['error']}"
    if not st["behind"]:
        return "Обновлений нет — у тебя последняя версия." if ru else "No updates — you're on the latest version."
    if st["dirty"]:
        return ("Есть незакоммиченные изменения — сначала сделай git commit, потом обновлю." if ru
                else "You have uncommitted changes — commit them first, then I'll update.")
    _c, old, _e = _git("rev-parse", "HEAD")
    code, _o, err = _git("merge", "--ff-only", f"origin/{BRANCH}")
    if code != 0:
        return (f"Не получилось обновиться без слияния (ветки разошлись): {err[:120]}" if ru
                else f"Couldn't fast-forward: {err[:120]}")
    ok, tail = _tests()
    if not ok:
        _git("reset", "--hard", old)
        return (f"Новая версия не прошла проверку — вернул как было. {tail}" if ru
                else f"The new version failed the tests — rolled back. {tail}")
    _c, new, _e = _git("rev-parse", "HEAD")
    _c, changed, _e = _git("diff", "--name-only", old, new)
    _save(dict(_load(), previous=old, current=new, ts=time.time()))
    deps = any(re.search(r"requirements.*\.txt$", f) for f in changed.splitlines())
    msg = (f"Обновился: {st['behind']} изменений, тесты прошли. Перезапусти меня, чтобы всё заработало." if ru else
           f"Updated: {st['behind']} changes, tests passed. Restart me to apply.")
    if deps:
        msg += (" Ещё поменялись зависимости: выполни pip install -r requirements.txt." if ru
                else " Dependencies changed: run pip install -r requirements.txt.")
    return msg


def rollback(ru: bool = True) -> str:
    prev = _load().get("previous")
    if not prev:
        return "Откатывать нечего — я ещё не обновлялся сам." if ru else "Nothing to roll back."
    _c, dirty, _e = _git("status", "--porcelain", "--untracked-files=no")
    if dirty.strip():
        return "Есть незакоммиченные изменения — откат не делаю." if ru else "Uncommitted changes — not rolling back."
    code, _o, err = _git("reset", "--hard", prev)
    if code != 0:
        return f"Не получилось откатить: {err[:120]}" if ru else f"Rollback failed: {err[:120]}"
    _save(dict(_load(), previous=None, current=prev, ts=time.time()))
    return "Вернул прошлую версию. Перезапусти меня." if ru else "Rolled back. Restart me."


def daily_check(announce) -> str:
    """Раз в день: есть новое на GitHub — сказать один раз про каждую новую версию."""
    st = check()
    if not st.get("ok") or not st.get("behind"):
        return ""
    head = st["commits"][0].split(" ", 1)[0] if st["commits"] else str(st["behind"])
    d = _load()
    if d.get("announced") == head:
        return ""
    _save(dict(d, announced=head))
    msg = f"Сэр, на GitHub есть обновление Atlas — изменений: {st['behind']}. Скажите «обнови себя», когда будет удобно."
    if announce:
        announce(msg)
    return msg


def start(announce) -> None:
    from core import scheduler
    scheduler.every(86400, lambda: daily_check(announce), "проверка обновлений", first=600, heavy=True,
                    essential=False)
    scheduler.start()


_APPLY = re.compile(r"^(?:атлас[,\s]+)?(?:обнови(?:сь| себя)|обновись|установи обновлени[ея]|update yourself)\b", re.I)
_CHECK = re.compile(r"(?:есть|какие) (?:ли )?обновлени[яе]|проверь обновлени[яе]|any updates|check for updates", re.I)
_BACK = re.compile(r"откати (?:обновление|версию)|верни (?:прошлую|старую) версию|roll ?back (?:the )?update", re.I)


def intent(text: str):
    t = " ".join((text or "").split()).strip(" .!?")
    if _BACK.search(t):
        return "rollback"
    if _APPLY.match(t):
        return "apply"
    if _CHECK.search(t) and not re.search(r"windows|винд|драйвер|приложени|телефон", t, re.I):
        return "check"
    return None


def handle(text: str):
    it = intent(text)
    if not it:
        return None
    ru = bool(re.search(r"[а-яё]", text or "", re.I))
    if it == "apply":
        st = check()
        if not st["ok"] or not st["behind"] or st["dirty"]:
            return apply(ru)                              # нечего/нельзя — ответ сразу

        def work():
            msg = apply(ru)
            try:
                from brain import state
                if state.announce.get("fn"):
                    state.announce["fn"](msg)
            except Exception:
                print(f"[обновления] {msg}")
        import threading
        threading.Thread(target=work, daemon=True, name="atlas-update").start()
        return (f"Обновляюсь: {st['behind']} изменений. Проверю тестами — пара минут, скажу, когда закончу." if ru
                else f"Updating: {st['behind']} changes. Running tests — a couple of minutes, I'll tell you when done.")
    if it == "rollback":
        return rollback(ru)
    st = check()
    if not st["ok"]:
        return f"Не смог связаться с GitHub: {st['error']}" if ru else f"Couldn't reach GitHub: {st['error']}"
    if not st["behind"]:
        return "Обновлений нет — последняя версия." if ru else "You're up to date."
    return (f"Есть обновление: {st['behind']} изменений. Скажи «обнови себя»." if ru
            else f"{st['behind']} updates available. Say 'update yourself'.")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "apply":
        print(apply())
    elif cmd == "rollback":
        print(rollback())
    else:
        st = check()
        print(json.dumps(st, ensure_ascii=False, indent=1))
