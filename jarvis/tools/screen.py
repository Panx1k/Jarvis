"""Экран и мышь: «что у меня на экране?», «прочитай, что здесь написано», прокрутка и клик.

Снимок берётся с монитора активного окна (не окна JARVIS). Описание экрана делает облачный мозг (Gemini видит
картинку); прочитать текст можно и без интернета — встроенным распознаванием текста Windows (OCR).
Снимок никуда не сохраняется и отправляется, только когда пользователь сам попросил посмотреть на экран.
"""
from __future__ import annotations

import ctypes
import io
import logging
import time
from ctypes import wintypes

from jarvis.tools.base import ToolResult, tool

log = logging.getLogger("jarvis.screen")


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def _target_window() -> int:
    from jarvis.utils import winapi

    user32 = ctypes.windll.user32
    tracker = winapi.foreground
    if tracker is not None and tracker.own_window_active() and tracker.last_foreign:
        return tracker.last_foreign
    return user32.GetForegroundWindow()


def capture(max_side: int = 1600):
    """Снимок монитора, на котором активное окно (PIL.Image, уменьшенный до max_side)."""
    from PIL import ImageGrab

    user32 = ctypes.windll.user32
    hwnd = _target_window()
    bbox = None
    if hwnd:
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
            r = info.rcMonitor
            bbox = (r.left, r.top, r.right, r.bottom)
    img = ImageGrab.grab(bbox=bbox, all_screens=True) if bbox else ImageGrab.grab()
    img = img.convert("RGB")
    scale = max_side / max(img.size)
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    return img


def jpeg(img, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def ocr(img, lang: str = "ru") -> str:
    """Текст с картинки встроенным OCR Windows (офлайн). Пустая строка — не получилось."""
    try:
        import asyncio

        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError:
        return ""
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    from PIL import Image

    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(bgra)
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, rgba.width,
                                                    rgba.height)
    engine = OcrEngine.try_create_from_language(Language(lang)) or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""

    async def run():
        return await engine.recognize_async(bitmap)

    result = asyncio.run(run())
    return "\n".join(line.text for line in result.lines).strip()


READ_PROMPT = ("Это снимок экрана пользователя. Прочитай главный текст на экране (заголовок, сообщение, абзац, "
               "на который скорее всего смотрит пользователь). Перескажи его коротко и естественно для озвучки, "
               "максимум 4 предложения, без markdown. Меню, панели задач и кнопки не перечисляй.")
DESCRIBE_PROMPT = ("Это снимок экрана пользователя. Ответь коротко (1–3 предложения, для озвучки, без markdown), "
                   "что на нём: какая программа или сайт открыты и что главное видно. Вопрос пользователя: {q}")


def _speakable(text: str, limit: int = 600) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), limit // 2) + 1].strip() + " …"


@tool("screen_read", "Посмотреть на экран пользователя: mode=describe — что сейчас на экране (какая программа, "
      "что видно, ответ на вопрос о том, что на экране); mode=read — прочитать текст, который там написан. "
      "Используй, когда спрашивают «что у меня на экране», «прочитай, что здесь написано», «что это за окно».",
      params={"mode": {"type": "string", "enum": ["describe", "read"], "description": "describe или read"},
              "question": {"type": "string", "description": "Вопрос пользователя об экране (необязательно)"}},
      announce="Смотрю на экран", category="screen",
      patterns=[r"(?P<mode>прочитай|прочти|зачитай|прочитать)[,\s]+(?:мне\s+)?(?:что\s+(?:тут|здесь|там|на\s+экране)"
                r"\s+)?(?:написано|текст|что\s+написано|с\s+экрана|экран)",
                r"^(?:что|чё|че)\s+(?:у\s+меня\s+)?(?:сейчас\s+)?(?:на\s+экране|открыто\s+на\s+экране)$",
                r"^(?:посмотри|глянь)\s+(?:на\s+)?(?:мой\s+)?экран",
                r"^(?:что|чё)\s+(?:это\s+)?(?:за\s+)?(?:окно|программа)\s+(?:у\s+меня\s+)?(?:открыт\w*|на\s+экране)$",
                r"^опиши\s+(?:мой\s+|что\s+на\s+)?экран\w*$"])
def screen_read(ctx, mode: str = "describe", question: str = "") -> ToolResult:
    mode = "read" if (mode or "").lower().startswith(("read", "прочит", "прочт", "зачит")) else "describe"
    time.sleep(0.15)
    try:
        img = capture(max_side=3840)
    except Exception as exc:
        log.warning("Снимок экрана не получился: %s", exc)
        return ToolResult(False, "Не получилось сделать снимок экрана.")
    vision = getattr(ctx, "vision", None)
    if vision is not None:
        prompt = READ_PROMPT if mode == "read" else DESCRIBE_PROMPT.format(q=question or "что у меня на экране?")
        small = img.copy()
        small.thumbnail((1600, 1600))
        try:
            answer = vision(prompt, jpeg(small))
            if answer:
                return ToolResult(True, _speakable(answer), {"mode": mode, "via": "vision"})
        except Exception as exc:
            log.info("Облачное зрение недоступно (%s) — читаю текст офлайн", type(exc).__name__)
    text = ocr(img)
    if not text:
        if mode == "read":
            return ToolResult(False, "Не смог разобрать текст на экране.")
        return ToolResult(False, "Не могу посмотреть на экран: облачный мозг сейчас недоступен, а текста на "
                                 "экране не нашёл.")
    if mode == "describe":
        return ToolResult(True, "Описать картинку без облачного мозга не могу, но на экране написано: "
                          + _speakable(text, 400), {"mode": mode, "via": "ocr"})
    return ToolResult(True, _speakable(text), {"mode": mode, "via": "ocr", "text": text[:2000]})


MOUSEEVENTF = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


def _mouse(flags: int, data: int = 0) -> None:
    from jarvis.utils import winapi

    inp = winapi.INPUT(type=0)
    inp.u.mi = winapi.MOUSEINPUT(dx=0, dy=0, mouseData=ctypes.c_uint32(data).value, dwFlags=flags, time=0,
                                 dwExtraInfo=0)
    winapi._send([inp])


@tool("scroll", "Прокрутить колёсиком мыши в активном окне: direction=down/up, amount — сколько «щелчков» (1–30).",
      params={"direction": {"type": "string", "enum": ["down", "up"]},
              "amount": {"type": "integer", "description": "Сколько щелчков колеса, по умолчанию 5"}},
      announce="Прокручиваю", category="screen",
      patterns=[r"^(?:прокрути|пролистай|листай|скролл?ь?|мотай|промотай)\s*(?:страницу\s+)?(?P<direction>вниз|вверх|"
                r"наверх|ниже|выше)?(?:\s+страницу)?$"])
def scroll(ctx, direction: str = "down", amount: int = 5) -> ToolResult:
    from jarvis.utils import winapi

    up = (direction or "down").lower().startswith(("up", "вверх", "наверх", "выше"))
    winapi.restore_foreground()
    steps = max(1, min(30, int(amount or 5)))
    for _ in range(steps):
        _mouse(0x0800, 120 if up else -120)
        time.sleep(0.015)
    return ToolResult(True, "Прокрутил " + ("вверх." if up else "вниз."))


@tool("mouse_click", "Щёлкнуть мышью там, где сейчас курсор: button=left/right/middle, double=true — двойной щелчок.",
      params={"button": {"type": "string", "enum": ["left", "right", "middle"]},
              "double": {"type": "boolean"}},
      announce="Щёлкаю мышью", category="screen",
      patterns=[r"^(?:кликни|щ[её]лкни|нажми)\s+(?P<button>правой|левой)?\s*(?:кнопкой\s+)?мыш\w*$",
                r"^(?P<double>двойной)\s+(?:клик|щелчок)$"])
def mouse_click(ctx, button: str = "left", double: bool = False) -> ToolResult:
    b = (button or "left").lower()
    key = "right" if b.startswith(("right", "прав")) else "middle" if b.startswith(("middle", "сред")) else "left"
    down, up = MOUSEEVENTF[key]
    for _ in range(2 if double else 1):
        _mouse(down)
        _mouse(up)
        time.sleep(0.05)
    return ToolResult(True, "Щёлкнул.")
