"""
Конспект документа (PDF, Word, PowerPoint, EPUB…) или веб-страницы — в той же библиотеке «Записи».

    import_doc(path)              — файл с компьютера
    import_text(text, title, …)   — готовый текст (страница по ссылке, выделенное)

В окне «Записи»: конспект, полный текст, «Открыть документ», вопросы по тексту — как у видео.
Сам файл не копируется; при удалении записи он не удаляется.
"""
import os
import threading
import time
import uuid
from datetime import datetime


def _jobs():
    from core import media_import
    return media_import._jobs


def _finish(job, title, text, source, notes_md, announce, kind="doc", url="", pages=0, minutes=0):
    from core import meeting_notes as M
    started = time.time()
    rid = M._new_id(started, title)
    base = os.path.join(M.folder(), rid)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(text)
    head = f"{datetime.now().strftime('%d.%m.%Y %H:%M')}"
    head += f" · {pages} стр." if pages else ""
    head += f" · {url}" if url else f" · файл: {os.path.basename(source)}" if source else ""
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{head}\n\n{notes_md}\n")
    entry = {"id": rid, "title": title, "started": started, "minutes": minutes or max(1, round(len(text) / 1000 / 1.2)),
             "mic": False, "imported": True, "kind": kind, "source": source or "", "url": url, "pages": pages,
             "txt": base + ".txt", "md": base + ".md", "audio": "", "video": ""}
    M._save_entry(entry)
    job.update(stage="готово", pct=100, id=rid)
    M._s["notes_ready"] = rid
    M._changed()
    msg = f"Конспект «{title}» готов — он в окне «Записи»."
    print(f"[записи] {msg}")
    if announce:
        announce(msg)
    return rid


def import_text(text: str, title: str, announce=None, ask=None, source: str = "", url: str = "", kind: str = "doc",
                pages: int = 0, wait: bool = False) -> str:
    """Готовый текст → конспект в фоне. → id задачи."""
    from core import meeting_notes as M, text_notes
    jid = uuid.uuid4().hex[:8]
    title = (title or "Текст").strip()[:80]
    _jobs()[jid] = {"title": title, "pct": 5, "stage": "конспект", "error": "", "path": source or url}
    M._changed()

    def run():
        job = _jobs()[jid]
        try:
            if not (text or "").strip():
                raise RuntimeError("в документе не нашлось текста")
            notes_md = text_notes.notes(text, "notes", ask=ask, progress=lambda p: job.update(pct=5 + int(p * 0.9)))
            _finish(job, title, text, source, notes_md, announce, kind=kind, url=url, pages=pages)
        except Exception as e:
            job.update(stage="ошибка", error=str(e)[:200])
            print(f"[записи] {title}: не получилось — {e}")
            M._changed()
            if announce:
                announce(f"Не получилось сделать конспект «{title}»: {str(e)[:120]}")
        finally:
            threading.Timer(30, lambda: _jobs().pop(jid, None)
                            if _jobs().get(jid, {}).get("stage") in ("готово", "ошибка") else None).start()
    t = threading.Thread(target=run, daemon=True, name=f"doc-{jid}")
    t.start()
    if wait:
        t.join()
    return jid


def import_doc(path: str, title: str = None, announce=None, ask=None, wait: bool = False, reader=None) -> str:
    """Файл-документ → полный текст + конспект (в фоне). → id задачи."""
    import re
    from core import doc_text, meeting_notes as M
    title = (title or re.sub(r"[_\-]+", " ", os.path.splitext(os.path.basename(path))[0]).strip())[:80] or "Документ"
    jid = uuid.uuid4().hex[:8]
    _jobs()[jid] = {"title": title, "pct": 2, "stage": "читаю", "error": "", "path": path}
    M._changed()

    def run():
        job = _jobs()[jid]
        try:
            d = (reader or doc_text.read)(path)
            job.update(stage="конспект", pct=10)
            M._changed()
            if not d["text"].strip():
                raise RuntimeError("в документе не нашлось текста")
            from core import text_notes
            notes_md = text_notes.notes(d["text"], "notes", ask=ask,
                                        progress=lambda p: job.update(pct=10 + int(p * 0.85)))
            _finish(job, title, d["text"], path, notes_md, announce, pages=d.get("pages") or 0)
        except Exception as e:
            job.update(stage="ошибка", error=str(e)[:200])
            print(f"[записи] {title}: не получилось — {e}")
            M._changed()
            if announce:
                announce(f"Не получилось сделать конспект «{title}»: {str(e)[:120]}")
        finally:
            threading.Timer(30, lambda: _jobs().pop(jid, None)
                            if _jobs().get(jid, {}).get("stage") in ("готово", "ошибка") else None).start()
    t = threading.Thread(target=run, daemon=True, name=f"doc-{jid}")
    t.start()
    if wait:
        t.join()
    return jid
