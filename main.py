"""J.A.R.V.I.S. — точка входа.

    python main.py              — графический интерфейс
    python main.py --cli        — консольный (текстовый) режим
    python main.py --list-mics  — список микрофонов
    python main.py --tools      — список инструментов
"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from jarvis.config import LOG_DIR


def setup_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOG_DIR / "jarvis.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
    ]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stderr))
    from jarvis.utils.secrets import RedactingFilter

    for handler in handlers:
        handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)
    for noisy in ("httpx", "httpx2", "httpcore", "urllib3", "comtypes", "asyncio", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run_cli(voice: bool) -> None:
    import threading

    from jarvis.core.assistant import Assistant, AssistantListener

    done = threading.Event()

    class ConsoleListener(AssistantListener):
        def on_message(self, role, text):
            if role == "assistant":
                print(f"\nJARVIS: {text}")
            elif role == "system":
                print(f"\n[!] {text}")

        def on_action(self, action_id, text, status, detail=""):
            mark = {"running": "…", "ok": "✓", "error": "✗", "confirm": "?"}.get(status, status)
            if status != "running":
                print(f"   [{mark}] {text}")

        def on_confirm(self, question):
            if question:
                print(f"   [подтверждение] {question} (да/нет)")

        def on_state(self, state):
            if voice and state != "idle":
                print(f"   <{state.upper()}>")
            if state == "idle":
                done.set()

        def on_transcript(self, text):
            print(f"\n[распознано] {text}")

        def on_spoken(self, text):
            print(f"   🔊 {text}")

    assistant = Assistant(ConsoleListener(), enable_voice=voice)
    assistant.set_voice_replies(voice)
    info = assistant.info()
    print(f"J.A.R.V.I.S. — мозг: {info['brain']}; инструментов: {info['tools']}; "
          f"речь: {info['stt']} / {info['tts']}; активатор: {info.get('wake')}")
    for notice in info.get("notices", []):
        print(f"[!] {notice}")
    assistant.verify_brain()
    if voice and assistant.set_wake_word(True):
        print("Скажите «Jarvis, …». Пустая строка — push-to-talk (слушать фразу), «выход» — завершить.")
    else:
        print("Введите команду («выход» — завершить).")
    try:
        while True:
            try:
                text = input("\nВы: ").strip()
            except EOFError:
                break
            if text.lower() in ("выход", "exit", "quit"):
                break
            done.clear()
            if not text:
                if not voice:
                    continue
                assistant.listen()
            else:
                assistant.submit_text(text)
            done.wait(timeout=120)
    finally:
        assistant.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. — голосовой ассистент для Windows")
    parser.add_argument("--cli", action="store_true", help="консольный режим")
    parser.add_argument("--voice", action="store_true", help="в консольном режиме: голосовой ввод/вывод")
    parser.add_argument("--list-mics", action="store_true", help="показать микрофоны")
    parser.add_argument("--tools", action="store_true", help="показать инструменты")
    parser.add_argument("--keys", action="store_true", help="проверить API keys OpenAI (только номера и статус)")
    parser.add_argument("--voices", action="store_true", help="список встроенных голосов XTTS")
    parser.add_argument("--say", metavar="ТЕКСТ", help="произнести фразу текущим голосом (проба голоса)")
    parser.add_argument("--speaker", metavar="ИМЯ", help="с --say: встроенный голос XTTS для пробы")
    parser.add_argument("--verbose", action="store_true", help="лог в консоль")
    parser.add_argument("--spotify-login", action="store_true", help="подключить Spotify (вход через браузер)")
    parser.add_argument("--minimized", action="store_true", help="запустить сразу в фоне (мини-ядро)")
    parser.add_argument("--elevenlabs-voices", action="store_true", help="голоса аккаунта ElevenLabs (ID для .env)")
    parser.add_argument("--autostart", action="store_true", help="запуск вместе с Windows")
    args = parser.parse_args()
    setup_logging(args.verbose)
    from jarvis.config import Settings
    from jarvis.utils import net

    net.apply_proxy(Settings())
    net.watch(Settings())

    if args.elevenlabs_voices:
        from jarvis.voice.elevenlabs import list_voices
        try:
            voices = list_voices()
        except Exception as exc:
            print(f"ElevenLabs: {exc}")
            return
        for v in voices:
            labels = ", ".join(f"{k}: {val}" for k, val in v["labels"].items())
            print(f"{v['id']}  {v['name']}  [{v['category']}]  {labels}")
        print("\nВпишите нужный ID в .env: ELEVENLABS_VOICE_ID=…  и TTS_ENGINE=elevenlabs")
        return
    if args.spotify_login:
        from jarvis.services import spotify
        client = spotify.get()
        try:
            print(client.login_async())
        except spotify.SpotifyError as exc:
            print(exc)
            return
        ok = client.wait_login()
        print("Spotify подключён." if ok else "Вход в Spotify не выполнен.")
        if ok:
            print(f"Аккаунт: {client.me().get('display_name', '')}")
        return

    if args.list_mics:
        from jarvis.voice.recorder import list_microphones
        print("\n".join(list_microphones()))
        return
    if args.tools:
        from jarvis.config import PLUGINS_DIR
        from jarvis.tools.base import load_builtin_tools, load_plugins, registry
        load_builtin_tools()
        load_plugins(PLUGINS_DIR)
        for t in sorted(registry.all(), key=lambda t: (t.category, t.name)):
            flag = " [подтверждение]" if t.dangerous else ""
            print(f"{t.category:10} {t.name:24} {t.description[:90]}{flag}")
        return
    if args.voices or args.say:
        import threading

        from jarvis.voice.lang import detect_lang
        from jarvis.voice.xtts import XttsTTS
        engine = XttsTTS(speaker=args.speaker)
        if args.voices:
            print("\n".join(engine.speakers()) or engine.error)
            return
        engine.speakers()
        if not engine.ready:
            print(engine.error)
            return
        print(f"Голос: {engine.name}")
        engine.speak(args.say, threading.Event(), detect_lang(args.say))
        return
    if args.keys:
        from jarvis.brain.openai_brain import OpenAIBrain
        from jarvis.config import Settings
        from jarvis.core.assistant import Runtime
        from jarvis.core.context import DialogContext
        from jarvis.tools.base import load_builtin_tools, registry
        load_builtin_tools()
        brain = OpenAIBrain(Runtime(Settings(), DialogContext(), registry, apps=None))
        if brain.error:
            print(brain.error)
            return
        ok, msg = brain.check_connection()
        print(brain.keys.status_text())
        print(msg)
        return
    if args.cli:
        run_cli(args.voice)
        return
    from jarvis.ui.app import run_gui
    sys.exit(run_gui(True if args.minimized else None))


if __name__ == "__main__":
    main()
