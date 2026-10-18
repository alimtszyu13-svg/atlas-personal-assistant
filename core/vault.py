"""
Ключи Atlas — в Диспетчер учётных данных Windows, а не открытым текстом в .env.

    python -m core.vault migrate   — перенести ключи из .env в хранилище Windows; в .env останется KEY=@vault
    python -m core.vault status    — какие ключи где (только имена, значения не показываются)
    python -m core.vault restore   — вернуть значения обратно в .env (если переезжаешь на другой компьютер)

Хранилище Windows шифрует значения под твоей учётной записью: другой пользователь компьютера и программа,
случайно прочитавшая .env, ключей не увидят. При запуске Atlas load() достаёт их в память процесса.
Также redact(): ключи и токены в логах заменяются на *** (журнал можно спокойно присылать).
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
PLACEHOLDER = "@vault"
PREFIX = "Atlas/"
CHUNK = 1200                      # символов в одной записи (у Windows предел 2560 байт)
SECRET_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|AUTH|JSON|COOKIE|PIN)$", re.I)
_TOKEN_SHAPES = re.compile(r"\b(?:gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_\-]{20,}|csk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,}|"
                           r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|hf_[A-Za-z0-9]{30,}|"
                           r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}|ya29\.[A-Za-z0-9_\-]{20,}|"
                           r"1//[A-Za-z0-9_\-]{30,})")
_s = {"backend": None, "values": [], "rx": None}


# --- хранилища -------------------------------------------------------------------------------------------------
class Memory:
    """Для тестов и как запасной вариант (только в памяти процесса)."""
    name = "memory"

    def __init__(self):
        self.d = {}

    def read(self, target):
        return self.d.get(target)

    def write(self, target, value):
        self.d[target] = value

    def delete(self, target):
        self.d.pop(target, None)


class WinCred:
    """Диспетчер учётных данных Windows через advapi32 (без сторонних библиотек)."""
    name = "Windows Credential Manager"

    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        self.c = ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR), ("Comment", wt.LPWSTR),
                        ("LastWritten", FILETIME), ("CredentialBlobSize", wt.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wt.DWORD),
                        ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                        ("UserName", wt.LPWSTR)]
        self.CRED = CREDENTIALW
        self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.adv.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        self.adv.CredReadW.restype = wt.BOOL
        self.adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wt.DWORD]
        self.adv.CredWriteW.restype = wt.BOOL
        self.adv.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
        self.adv.CredDeleteW.restype = wt.BOOL
        self.adv.CredFree.argtypes = [ctypes.c_void_p]

    def read(self, target):
        c = self.c
        p = c.POINTER(self.CRED)()
        if not self.adv.CredReadW(target, 1, 0, c.byref(p)):         # 1 = CRED_TYPE_GENERIC
            return None
        try:
            cr = p.contents
            return c.string_at(cr.CredentialBlob, cr.CredentialBlobSize).decode("utf-8")
        finally:
            self.adv.CredFree(p)

    def write(self, target, value):
        c = self.c
        blob = value.encode("utf-8")
        buf = (c.c_ubyte * max(1, len(blob))).from_buffer_copy(blob or b"\0")
        cr = self.CRED()
        cr.Type = 1
        cr.TargetName = target
        cr.CredentialBlobSize = len(blob)
        cr.CredentialBlob = c.cast(buf, c.POINTER(c.c_ubyte))
        cr.Persist = 2                                                 # CRED_PERSIST_LOCAL_MACHINE
        cr.UserName = "atlas"
        if not self.adv.CredWriteW(c.byref(cr), 0):
            raise OSError(f"CredWrite: ошибка {c.get_last_error()}")

    def delete(self, target):
        self.adv.CredDeleteW(target, 1, 0)


def backend():
    if _s["backend"] is None:
        if sys.platform == "win32" and os.getenv("ATLAS_TESTING") != "1":
            try:
                _s["backend"] = WinCred()
            except Exception as e:
                print(f"[ключи] хранилище Windows недоступно: {e}")
                _s["backend"] = Memory()
        else:
            _s["backend"] = Memory()
    return _s["backend"]


def set_backend(b) -> None:
    _s["backend"] = b


def get(name: str):
    b = backend()
    head = b.read(PREFIX + name)
    if head is None:
        return None
    m = re.match(r"^@parts:(\d+)$", head)
    if not m:
        return head
    parts = [b.read(f"{PREFIX}{name}#{i}") for i in range(int(m.group(1)))]
    return None if any(p is None for p in parts) else "".join(parts)


def put(name: str, value: str) -> None:
    b = backend()
    if len(value) <= CHUNK:
        b.write(PREFIX + name, value)
        return
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)]
    for i, p in enumerate(parts):
        b.write(f"{PREFIX}{name}#{i}", p)
    b.write(PREFIX + name, f"@parts:{len(parts)}")


def remove(name: str) -> None:
    b = backend()
    head = b.read(PREFIX + name)
    m = re.match(r"^@parts:(\d+)$", head or "")
    if m:
        for i in range(int(m.group(1))):
            b.delete(f"{PREFIX}{name}#{i}")
    b.delete(PREFIX + name)


# --- .env ------------------------------------------------------------------------------------------------------
_LINE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")


def _parse_value(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return re.sub(r"\s+#.*$", "", v)


def read_env(path: str = None) -> list:
    """→ [(строка, имя|None, значение|None)] — порядок и комментарии сохраняются."""
    try:
        with open(path or ENV, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except Exception:
        return []
    out = []
    for ln in lines:
        m = _LINE.match(ln)
        out.append((ln, m.group(2), _parse_value(m.group(4))) if m and not ln.lstrip().startswith("#") else (ln, None, None))
    return out


def _write_env(lines: list, path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def is_secret(name: str) -> bool:
    return bool(SECRET_NAME.search(name or "")) or name in ("SUPABASE_SERVICE_KEY",)


def load(path: str = None) -> int:
    """При запуске: KEY=@vault в .env → настоящее значение из хранилища в os.environ. → сколько достал."""
    n = 0
    for _ln, name, val in read_env(path):
        if not name:
            continue
        if val == PLACEHOLDER or os.environ.get(name) == PLACEHOLDER:
            v = get(name)
            if v:
                os.environ[name] = v
                n += 1
            else:
                print(f"[ключи] {name}: в .env стоит {PLACEHOLDER}, но в хранилище Windows значения нет")
                os.environ[name] = ""             # пусто, а не «@vault»: dotenv потом не перезапишет
    _refresh_redaction()
    if n:
        print(f"[ключи] из хранилища Windows: {n}")
    return n


def migrate(path: str = None) -> list:
    """Перенести ключи из .env в хранилище. Строка меняется только если значение прочиталось обратно точно."""
    path = path or ENV
    if isinstance(backend(), Memory) and os.getenv("ATLAS_TESTING") != "1":
        print("[ключи] постоянного хранилища нет (это не Windows) — ничего не переношу")
        return []
    rows = read_env(path)
    moved, lines = [], []
    for ln, name, val in rows:
        if name and val and val != PLACEHOLDER and is_secret(name):
            put(name, val)
            if get(name) == val:
                m = _LINE.match(ln)
                lines.append(f"{m.group(1)}{name}{m.group(3)}{PLACEHOLDER}")
                moved.append(name)
                os.environ[name] = val
                continue
            print(f"[ключи] {name}: не удалось проверить запись — оставил в .env")
        lines.append(ln)
    if moved:
        _write_env(lines, path)
    _refresh_redaction()
    return moved


def restore(path: str = None) -> list:
    path = path or ENV
    back, lines = [], []
    for ln, name, val in read_env(path):
        if name and val == PLACEHOLDER:
            v = get(name)
            if v is not None:
                m = _LINE.match(ln)
                q = '"' if re.search(r"[\s#'\"]", v) else ""
                lines.append(f"{m.group(1)}{name}{m.group(3)}{q}{v}{q}")
                back.append(name)
                continue
        lines.append(ln)
    if back:
        _write_env(lines, path)
    return back


def status(path: str = None) -> dict:
    out = {"vault": [], "plain": [], "missing": [], "backend": backend().name}
    for _ln, name, val in read_env(path):
        if not name or not is_secret(name):
            continue
        if val == PLACEHOLDER:
            out["vault" if get(name) else "missing"].append(name)
        elif val:
            out["plain"].append(name)
    return out


# --- логи без ключей -------------------------------------------------------------------------------------------
def _refresh_redaction() -> None:
    vals = sorted({v for k, v in os.environ.items() if is_secret(k) and v and len(v) >= 8 and v != PLACEHOLDER},
                  key=len, reverse=True)
    _s["values"] = vals
    _s["rx"] = re.compile("|".join(re.escape(v) for v in vals)) if vals else None


def redact(text: str) -> str:
    if not text:
        return text
    if _s["rx"] is not None:
        text = _s["rx"].sub("***", text)
    return _TOKEN_SHAPES.sub("***", text)


def _main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "migrate":
        moved = migrate()
        print(f"Перенёс в {backend().name}: {', '.join(moved) or 'нечего переносить'}.")
        if moved:
            print("В .env вместо значений теперь @vault. Atlas достанет их сам при запуске.")
    elif cmd == "restore":
        back = restore()
        print(f"Вернул в .env: {', '.join(back) or 'нечего'}.")
    else:
        st = status()
        print(f"Хранилище: {st['backend']}")
        print(f"  в хранилище: {', '.join(st['vault']) or '—'}")
        print(f"  открытым текстом в .env: {', '.join(st['plain']) or '—'}")
        if st["missing"]:
            print(f"  @vault, но значения нет: {', '.join(st['missing'])}")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _main(sys.argv)
