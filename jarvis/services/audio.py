"""Системная громкость (Core Audio через pycaw)."""
from __future__ import annotations

import threading

_local = threading.local()


def _endpoint():
    if not getattr(_local, "com", False):
        import comtypes
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        _local.com = True
    from pycaw.pycaw import AudioUtilities

    device = AudioUtilities.GetSpeakers()
    ev = getattr(device, "EndpointVolume", None)
    if ev is None:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume

        interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        ev = cast(interface, POINTER(IAudioEndpointVolume))
    return ev


def get_volume() -> int:
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(percent: int) -> int:
    percent = max(0, min(100, int(percent)))
    ev = _endpoint()
    ev.SetMasterVolumeLevelScalar(percent / 100.0, None)
    if percent > 0 and ev.GetMute():
        ev.SetMute(0, None)
    return percent


def change_volume(delta: int) -> int:
    return set_volume(get_volume() + int(delta))


def set_mute(mute: bool) -> None:
    _endpoint().SetMute(1 if mute else 0, None)


def is_muted() -> bool:
    return bool(_endpoint().GetMute())
