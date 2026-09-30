"""Discord через UI Automation Windows: видеть и нажимать кнопки звонка, как человек, без переключения окна.

Знает текущее состояние (микрофон заглушён? звук выключен? идёт звонок? демонстрация экрана?), поэтому
«выключи микрофон» выключает, а не переключает наугад. Названия кнопок — русские и английские версии Discord.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

log = logging.getLogger("jarvis.discord")

MIC = ("заглушить", "снять заглушение", "включить микрофон", "выключить микрофон", "откл. микрофон",
       "вкл. микрофон", "mute", "unmute")
DEAFEN = ("откл. звук", "вкл. звук", "отключить звук", "включить звук", "deafen", "undeafen")
SHARE = ("продемонстрируйте свой экран", "демонстрация экрана", "поделиться экраном", "share your screen",
         "share screen")
STOP_SHARE = ("прекратить стрим", "остановить трансляцию", "прекратить трансляцию", "остановить демонстрацию", "прекратить демонстрацию",
              "остановить стрим", "stop streaming", "stop sharing")
GO_LIVE = ("прямой эфир", "начать трансляцию", "в эфир", "go live", "начать стрим", "транслировать")
SCREENS_TAB = ("экраны", "экран", "screens", "screen")
DISCONNECT = ("отключиться", "disconnect")
CAMERA_ON = ("включить камеру", "turn on camera")
CAMERA_OFF = ("выключить камеру", "turn off camera")


@dataclass
class Button:
    name: str
    element: object
    pressed: bool | None


class DiscordUI:
    def __init__(self):
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA

        self.UIA = UIA
        self.uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

    def _root(self):
        from jarvis.utils import winapi

        hwnd = winapi.find_window("Discord", {"discord.exe"})
        return self.uia.ElementFromHandle(hwnd) if hwnd else None

    def buttons(self, root=None) -> list[Button]:
        UIA = self.UIA
        root = root or self._root()
        if root is None:
            return []
        cond = self.uia.CreateOrCondition(
            self.uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_ButtonControlTypeId),
            self.uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_TabItemControlTypeId))
        found = []
        for attempt in range(3):
            items = root.FindAll(UIA.TreeScope_Descendants, cond)
            if items.Length > 5:
                break
            time.sleep(0.6)
        for i in range(items.Length):
            el = items.GetElement(i)
            name = (el.CurrentName or "").strip()
            if not name:
                continue
            aria = ""
            try:
                aria = el.GetCurrentPropertyValue(UIA.UIA_AriaPropertiesPropertyId) or ""
            except Exception:
                pass
            m = re.search(r"(?:pressed|checked)=(true|false)", aria)
            found.append(Button(name, el, (m.group(1) == "true") if m else None))
        return found

    @staticmethod
    def find(buttons: list[Button], names, exact: bool = False) -> Button | None:
        for b in buttons:
            low = b.name.lower()
            if any((low == n) if exact else low.startswith(n) for n in names):
                return b
        return None

    def click(self, button: Button) -> bool:
        UIA = self.UIA
        el = button.element
        for pattern_id, iface, method in ((UIA.UIA_InvokePatternId, UIA.IUIAutomationInvokePattern, "Invoke"),
                                          (UIA.UIA_TogglePatternId, UIA.IUIAutomationTogglePattern, "Toggle"),
                                          (UIA.UIA_SelectionItemPatternId, UIA.IUIAutomationSelectionItemPattern,
                                           "Select")):
            try:
                pattern = el.GetCurrentPattern(pattern_id)
                if pattern:
                    getattr(pattern.QueryInterface(iface), method)()
                    return True
            except Exception:
                continue
        return False

    def state(self) -> dict | None:
        bs = self.buttons()
        if not bs:
            return None
        toggles = [b for b in bs if b.pressed is not None]
        mic = self.find(toggles, MIC, exact=True)
        deaf = self.find(toggles, DEAFEN, exact=True)
        return {"mic_muted": mic.pressed if mic else None, "deafened": deaf.pressed if deaf else None,
                "in_call": self.find(bs, DISCONNECT, exact=True) is not None,
                "sharing": self.find(bs, STOP_SHARE) is not None}

    def _find_fresh(self, names, exact: bool = True, tries: int = 3, toggle: bool = False) -> Button | None:
        """Кнопка по названию; дерево Discord иногда достраивается не сразу — спрашиваем ещё раз.
        toggle=True — только переключатель с состоянием: кнопок «Заглушить» бывает несколько (в панели звонка
        есть такая же без состояния), нажимать нужно ту, что знает, включён микрофон или нет."""
        for attempt in range(tries):
            bs = self.buttons()
            if toggle:
                bs = [b for b in bs if b.pressed is not None]
            b = self.find(bs, names, exact)
            if b is not None:
                return b
            time.sleep(0.5)
        return None

    def set_toggle(self, names, want: bool | None) -> tuple[bool, bool | None]:
        """Нажать переключатель, если его состояние не то, что нужно. (удалось, новое состояние)."""
        b = self._find_fresh(names, toggle=True)
        if b is None:
            return False, None
        if want is not None and b.pressed == want:
            return True, want
        if not self.click(b):
            return False, b.pressed
        time.sleep(0.3)
        after = self._find_fresh(names, toggle=True, tries=2)
        return True, after.pressed if after else (not b.pressed)

    def share_screen(self, start: bool) -> str:
        bs = self.buttons()
        stop = self.find(bs, STOP_SHARE)
        if not start:
            if stop is None:
                return "not_sharing"
            return "stopped" if self.click(stop) else "failed"
        if stop is not None:
            return "already"
        share = self.find(bs, SHARE)
        if share is None:
            return "no_call"
        if not self.click(share):
            return "failed"
        deadline = time.time() + 6
        picked = False
        while time.time() < deadline:
            time.sleep(0.5)
            bs = self.buttons()
            if not picked:
                tab = self.find(bs, SCREENS_TAB, exact=True)
                if tab:
                    self.click(tab)
                    time.sleep(0.4)
                    bs = self.buttons()
                tile = next((b for b in bs if re.match(r"^(?:экран|screen|display|монитор)\s*\d", b.name.lower())),
                            None)
                if tile:
                    self.click(tile)
                    picked = True
                    time.sleep(0.4)
                    bs = self.buttons()
            go = self.find(bs, GO_LIVE)
            if go and self.click(go):
                return "started"
        return "picker"


_ui: DiscordUI | None = None


def get() -> DiscordUI | None:
    global _ui
    if _ui is None:
        try:
            _ui = DiscordUI()
        except Exception as exc:
            log.warning("UI Automation недоступна: %s", exc)
            return None
    return _ui
