"""Данные для HUD: система (CPU, RAM, GPU, VRAM, температура, сеть, аптайм) и сеть (VPN, интернет, пинг, IP).

Лёгкий опрос: системные показатели раз в 1.5 с и только пока главное окно видно; сеть (пинг, статус VPN)
— в фоновом потоке раз в 15 с. Недоступный показатель — None (интерфейс пишет «N/A»).
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("jarvis.monitor")


@dataclass
class SystemStats:
    cpu: float | None = None
    ram: float | None = None
    ram_text: str = "N/A"
    gpu: float | None = None
    vram: float | None = None
    vram_text: str = "N/A"
    temp: float | None = None
    net_down: float | None = None
    net_up: float | None = None
    uptime: str = "N/A"


@dataclass
class NetStats:
    vpn: bool | None = None
    vpn_text: str = "N/A"
    online: bool | None = None
    ping_ms: float | None = None
    ip: str | None = None
    extra: dict = field(default_factory=dict)


class _Gpu:
    """NVIDIA через NVML (nvidia-ml-py). Нет драйвера/библиотеки — все показатели N/A."""

    def __init__(self):
        self.handle = None
        try:
            import pynvml

            pynvml.nvmlInit()
            self.nv = pynvml
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception as exc:
            log.debug("NVML недоступен: %s", exc)

    def read(self) -> tuple[float | None, float | None, str, float | None]:
        if self.handle is None:
            return None, None, "N/A", None
        try:
            util = self.nv.nvmlDeviceGetUtilizationRates(self.handle).gpu
            mem = self.nv.nvmlDeviceGetMemoryInfo(self.handle)
            temp = self.nv.nvmlDeviceGetTemperature(self.handle, 0)
            used, total = mem.used / 1024 ** 3, mem.total / 1024 ** 3
            return float(util), used / total * 100, f"{used:.1f}/{total:.0f} ГБ", float(temp)
        except Exception:
            return None, None, "N/A", None


class SystemMonitor(QObject):
    stats = Signal(object)
    net = Signal(object)

    def __init__(self, settings, apps=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.apps = apps
        self._gpu = _Gpu()
        self._net_prev = psutil.net_io_counters()
        self._net_t = time.monotonic()
        psutil.cpu_percent(None)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._net_stop = threading.Event()
        self._net_thread: threading.Thread | None = None
        self._net_wake = threading.Event()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(1500)
            self._poll()
        if self._net_thread is None:
            self._net_thread = threading.Thread(target=self._net_loop, name="hud-net", daemon=True)
            self._net_thread.start()
        self._net_wake.set()

    def pause(self) -> None:
        """Главное окно скрыто — системные показатели не нужны."""
        self._timer.stop()

    def refresh_network(self) -> None:
        self._net_wake.set()

    def stop(self) -> None:
        self._timer.stop()
        self._net_stop.set()
        self._net_wake.set()

    def _poll(self) -> None:
        s = SystemStats()
        try:
            s.cpu = psutil.cpu_percent(None)
            vm = psutil.virtual_memory()
            s.ram = vm.percent
            s.ram_text = f"{vm.used / 1024 ** 3:.1f}/{vm.total / 1024 ** 3:.0f} ГБ"
        except Exception:
            pass
        s.gpu, s.vram, s.vram_text, s.temp = self._gpu.read()
        try:
            now, cur = time.monotonic(), psutil.net_io_counters()
            dt = max(0.1, now - self._net_t)
            s.net_down = (cur.bytes_recv - self._net_prev.bytes_recv) / dt / 1024
            s.net_up = (cur.bytes_sent - self._net_prev.bytes_sent) / dt / 1024
            self._net_prev, self._net_t = cur, now
        except Exception:
            pass
        try:
            up = int(time.time() - psutil.boot_time())
            d, h, m = up // 86400, up % 86400 // 3600, up % 3600 // 60
            s.uptime = (f"{d}д " if d else "") + f"{h:02d}:{m:02d}"
        except Exception:
            pass
        self.stats.emit(s)

    def _net_loop(self) -> None:
        while not self._net_stop.is_set():
            self._net_wake.clear()
            try:
                self.net.emit(self._read_net())
            except Exception as exc:
                log.debug("network hud: %s", exc)
            self._net_wake.wait(15)

    @staticmethod
    def _ping() -> tuple[bool | None, float | None]:
        """Время ответа сервера: TLS-рукопожатие с 1.1.1.1 (ICMP без прав администратора недоступен, а голое
        TCP-соединение через VPN-адаптер «отвечает» локально за 0 мс). Рукопожатие — это ~2 обмена с сервером,
        поэтому берём половину. Лучшее из двух попыток."""
        import ssl

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        best = None
        for _ in range(2):
            t = time.monotonic()
            try:
                with socket.create_connection(("1.1.1.1", 443), timeout=3) as raw:
                    with ctx.wrap_socket(raw, server_hostname="one.one.one.one"):
                        ms = (time.monotonic() - t) * 1000 / 2
                best = ms if best is None else min(best, ms)
            except (OSError, ssl.SSLError):
                continue
        return (best is not None), best

    def _read_net(self) -> NetStats:
        n = NetStats()
        n.online, n.ping_ms = self._ping()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                n.ip = s.getsockname()[0]
        except OSError:
            n.ip = None
        try:
            from jarvis.services.vpn import VpnController

            st = VpnController(self.settings, self.apps).status()
            n.vpn = st.connected
            n.vpn_text = "CONNECTED" if st.connected else ("DISCONNECTED" if st.connected is False else "N/A")
        except Exception:
            n.vpn, n.vpn_text = None, "N/A"
        return n
