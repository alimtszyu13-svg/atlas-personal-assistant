# Atlas — a voice assistant for a Windows PC

Atlas is a Jarvis-style voice assistant I'm building in Python. You say «Атлас», give a command in Russian or English, and it acts on the computer: it writes in messengers, plays music, finds files, arranges windows, reads notifications aloud and answers questions. It also has a phone companion app.

The project is a work in progress. This page describes what exists and what was measured, including the parts that still don't work well.

## How a command flows

```
microphone ─► wake word «Атлас» ─► speech-to-text ─► router ─► action ─► speech
              (local, Vosk +       (Whisper via     │
               2nd check)           Groq)           ├─ module claims it   (messengers, windows, files, recordings…)
                                                    ├─ fast command       (volume, music, apps — no AI model)
                                                    ├─ instant answer     (weather, time, maths)
                                                    └─ language model     (everything else; tool calling over ~84 skills)
```

The idea behind this layout is that a simple command should never wait for an AI model. Every module exposes a side-effect-free check: "is this phrase mine?". The router (`core/intent_router.py`) asks all of them and runs the first one that claims the phrase. Only phrases nobody claims go to the language model (`brain/`).

## Main parts

| Area | What it does | Where |
|---|---|---|
| Wake word | Local Vosk model, then a second full-vocabulary check so similar words don't wake it | `voice.py`, `core/wake_check.py` |
| Speech | Whisper (Groq) for recognition; Fish Audio / ElevenLabs / Silero for the voice. Replies are synthesized piece by piece, so speech starts before the whole answer is ready | `voice.py` |
| Brain | Tool-calling LLM (gpt-oss-120b on Cerebras/Groq, Gemini as a fallback), planner, memory | `brain/`, `core/memory.py` |
| Messengers | Writes and reads Telegram / WhatsApp / Discord by reading the window's accessibility tree (UI Automation), with OCR as a fallback. It drafts the message, asks «Отправить?», and only then sends | `core/messengers.py` |
| Notifications | Reads the Windows notification database and announces important messages. Calls go to a "call secretary" | `core/msg_watch.py`, `core/call_secretary.py` |
| Desktop UI | pywebview + Three.js: a ray-traced black hole scene (light bending, Doppler-shifted disk) | `atlas_ui.html`, `web_gui.py` |
| Phone | Progressive web app over Tailscale: talk to Atlas, control the PC, search files | `phone/` |

## Testing

```
python tests/run_all.py          # 422 tests in 33 suites, ~100 s, no internet or Windows needed
python tests/test_real_phrases.py   # 44 real phrases from my usage logs → where each one is routed
python doctor.py                 # checks keys, models, services, microphone (on the real PC)
python tests/live_check.py       # read-only check of the real messenger windows, file search, voice, Google
python tests/voice_bench.py      # speed of every voice engine on this PC
```

Most tests run against fake windows and fake models. Green tests prove the logic, not that a feature works on a real PC, and I learned that the hard way. So every bug found in real use first becomes a test, and three scripts (`doctor.py`, `live_check.py`, `voice_bench.py`) check the real machine.

## Measured, not guessed (October 2026)

From my own usage logs (133 commands, Oct 5–20):

- Time from the end of my speech to Atlas's first sound: median **4.2 s**, 90th percentile **9.9 s**. Most of it is speech synthesis, not the AI model (typical model step: 0.5–1.5 s).
- Out of 44 real command phrases, **32 are now handled without the language model** (`test_real_phrases.py`).

Voice synthesis on my PC (`voice_bench.py`, first audio ready):

| Engine | 12 chars | 51 chars | 107 chars |
|---|---|---|---|
| Fish Audio (current voice) | 1.16 s | 2.24 s | 3.87 s |
| Fish Audio, `latency=balanced` (now default) | first bytes 0.67 s | — | first bytes 0.78 s |
| ElevenLabs Flash v2.5 | 0.25 s | 0.44 s | — |
| Silero (local, offline) | 0.05 s | 0.12 s | 0.23 s |

Changes these measurements led to:
- Long replies are now spoken piece by piece. Before, an 86-character reply waited 4.3 s for the whole text to be synthesized.
- File search by name was walking every disk and took up to 35 s. Now it checks the index first, then home folders breadth-first, then other disks, all under time limits.
- The wake-word threshold was lowered from 0.9 to 0.6. The logs showed real «Атлас» calls being rejected and repeated 2–8 s later; the second check still filters noise.
- Messenger tools now report "NOT SENT" honestly. Before, the planner could say "message sent" when it had only drafted one.

## Known limitations (honest list)

- Speech start is still around 1–2 s, set by the cloud voice. A local voice (Silero) would be ~10× faster but sounds worse. That's an open decision.
- WhatsApp has no official personal API on PC, so it's controlled through its window. That's more fragile than an API. Telegram could move to its official API.
- The language model runs on free tiers and sometimes hits rate limits (HTTP 429).
- The project grew wide before it grew deep. The current focus is making five everyday scenarios work 10/10 instead of adding features.

## Running

Windows 10/11, Python 3.14. Install with `pip install -r requirements.txt`. API keys go into the Windows credential vault (`core/vault.py`) or `.env`. Then run `python main.py`.

Privacy: see `PRIVACY.md`. Google data is read-only and nothing is shared.
