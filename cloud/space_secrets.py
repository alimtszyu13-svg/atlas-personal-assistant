"""
Что вписать в секреты Hugging Face Space — из твоего .env и ключа сопряжения телефона.

    python -m cloud.space_secrets

Выводит значения на ЭТОТ экран. Никому их не показывай и не присылай в чат.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ("GROQ_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "PHONE_KEY")
OPTIONAL = ("CEREBRAS_API_KEY", "GEMINI_API_KEY", "FISH_API_KEY", "FISH_VOICES_RU", "FISH_VOICES_EN", "STT_LANGUAGE",
            "GOOGLE_TOKEN_JSON")


def collect() -> dict:
    try:
        from dotenv import dotenv_values
        env = dict(dotenv_values(os.path.join(ROOT, ".env")))
    except Exception:
        env = {}
    env = {k: v for k, v in env.items() if v}
    try:
        with open(os.path.join(ROOT, "phone_pairing.json"), encoding="utf-8") as f:
            env["PHONE_KEY"] = json.load(f).get("token", "")
    except Exception:
        pass
    try:                                              # вход в Google с компьютера — для Календаря и Gmail в облаке
        with open(os.path.join(ROOT, "token.json"), encoding="utf-8") as f:
            env["GOOGLE_TOKEN_JSON"] = json.dumps(json.load(f), separators=(",", ":"))
    except Exception:
        pass
    # голос Fish: первый русский/английский из FISH_VOICES_RU / FISH_VOICES_EN («Имя:id,…»)
    for lang in ("RU", "EN"):
        lst = env.get(f"FISH_VOICES_{lang}") or ""
        first = next((x.split(":", 1)[1].strip() for x in lst.split(",") if ":" in x), "")
        if first and not env.get(f"FISH_VOICE_{lang}"):
            env[f"FISH_VOICE_{lang}"] = first
    return env


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    env = collect()
    print("Hugging Face → твой Space → Settings → Variables and secrets → New secret.\n"
          "Имя — слева, значение — справа. Это ключи: никому не показывай.\n")
    missing = []
    for name in REQUIRED:
        if env.get(name):
            print(f"  {name} = {env[name]}")
        else:
            missing.append(name)
    print("\nПо желанию (быстрее модели, голос Fish):")
    for name in OPTIONAL:
        if env.get(name):
            print(f"  {name} = {env[name]}")
    if missing:
        print(f"\n✗ Не хватает: {', '.join(missing)}"
              + (" — PHONE_KEY появится после «Атлас, установи себя на телефон»." if "PHONE_KEY" in missing else ""))


if __name__ == "__main__":
    main()
