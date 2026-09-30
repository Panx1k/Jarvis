"""Spotify Web API: поиск и воспроизведение, очередь, лайки, плейлисты, перемешивание, повтор, громкость.

Подключение один раз:
  1. https://developer.spotify.com/dashboard → Create app. Redirect URI: http://127.0.0.1:8765/callback,
     API: «Web API». Client ID вписать в .env: SPOTIFY_CLIENT_ID=...
  2. Сказать «Jarvis, подключи Spotify» (или python main.py --spotify-login) и нажать «Принять» в браузере.

Вход по OAuth PKCE: пароль и client secret не нужны. Токены хранятся зашифрованными Windows (DPAPI) в
config/spotify_token.bin (файл в .gitignore) и никогда не выводятся в логи, чат и интерфейс.
Управление воспроизведением через API требует Spotify Premium.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests

from jarvis.config import ROOT, env
from jarvis.utils.secrets import register_secret

log = logging.getLogger("jarvis.spotify")

API = "https://api.spotify.com/v1"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TOKEN_FILE = ROOT / "config" / "spotify_token.bin"
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing user-library-read "
          "user-library-modify playlist-read-private playlist-read-collaborative user-read-recently-played")


class SpotifyError(Exception):
    """Ошибка для пользователя (без технических деталей)."""


def _protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(data, "JARVIS Spotify", None, None, None, 0)


def _unprotect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


class Spotify:
    def __init__(self):
        self._token: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._login_thread: threading.Thread | None = None
        self._load()

    @property
    def client_id(self) -> str:
        cid = env("SPOTIFY_CLIENT_ID") or ""
        if not cid:
            from dotenv import dotenv_values

            cid = (dotenv_values(ROOT / ".env").get("SPOTIFY_CLIENT_ID") or "").strip()
            if cid:
                os.environ["SPOTIFY_CLIENT_ID"] = cid
        return cid.strip()

    @property
    def redirect_uri(self) -> str:
        return env("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8765/callback") or "http://127.0.0.1:8765/callback"

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def connected(self) -> bool:
        return self.configured and bool(self._token.get("refresh_token"))

    def _load(self) -> None:
        try:
            self._token = json.loads(_unprotect(TOKEN_FILE.read_bytes()))
            for k in ("access_token", "refresh_token"):
                register_secret(self._token.get(k, ""))
        except FileNotFoundError:
            self._token = {}
        except Exception as exc:
            log.warning("Spotify: не удалось прочитать сохранённый вход (%s)", type(exc).__name__)
            self._token = {}

    def _save(self) -> None:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_bytes(_protect(json.dumps(self._token).encode()))
        for k in ("access_token", "refresh_token"):
            register_secret(self._token.get(k, ""))

    def login_async(self, timeout: float = 240) -> str:
        """Открыть страницу входа Spotify в браузере и в фоне дождаться подтверждения."""
        if not self.configured:
            raise SpotifyError("Spotify не настроен: нужен SPOTIFY_CLIENT_ID в .env (см. README).")
        if self._login_thread and self._login_thread.is_alive():
            return "Страница входа Spotify уже открыта в браузере."
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state})
        parsed = urllib.parse.urlparse(self.redirect_uri)
        result: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] == state:
                    result["code"] = q.get("code", [""])[0]
                    result["error"] = q.get("error", [""])[0]
                ok = bool(result.get("code"))
                body = ("<h2>JARVIS: Spotify подключён. Окно можно закрыть.</h2>" if ok
                        else "<h2>JARVIS: вход в Spotify не выполнен.</h2>").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 8765), Handler)
        server.timeout = 1

        def wait():
            deadline = time.time() + timeout
            try:
                while time.time() < deadline and not result:
                    server.handle_request()
            finally:
                server.server_close()
            if not result.get("code"):
                log.warning("Spotify: вход не выполнен (%s)", result.get("error") or "время ожидания истекло")
                return
            r = requests.post(TOKEN_URL, data={
                "grant_type": "authorization_code", "code": result["code"], "redirect_uri": self.redirect_uri,
                "client_id": self.client_id, "code_verifier": verifier}, timeout=15)
            if r.status_code != 200:
                log.warning("Spotify: обмен кода на токен не удался (HTTP %s)", r.status_code)
                return
            self._set_token(r.json())
            log.info("Spotify: подключён")
            from jarvis.core.knowledge import invalidate
            invalidate()

        self._login_thread = threading.Thread(target=wait, name="spotify-login", daemon=True)
        self._login_thread.start()
        import webbrowser

        webbrowser.open(url)
        return "Открыл страницу входа Spotify в браузере — нажмите «Принять»."

    def wait_login(self, timeout: float = 240) -> bool:
        if self._login_thread:
            self._login_thread.join(timeout)
        return self.connected

    def _set_token(self, data: dict) -> None:
        with self._lock:
            self._token["access_token"] = data["access_token"]
            self._token["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
            if data.get("refresh_token"):
                self._token["refresh_token"] = data["refresh_token"]
            self._save()

    def _refresh(self) -> None:
        r = requests.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": self.client_id,
                                           "refresh_token": self._token.get("refresh_token", "")}, timeout=15)
        if r.status_code != 200:
            log.warning("Spotify: не удалось обновить вход (HTTP %s)", r.status_code)
            if r.status_code in (400, 401):
                self._token = {}
                TOKEN_FILE.unlink(missing_ok=True)
            raise SpotifyError("Вход в Spotify устарел — скажите «подключи Spotify».")
        self._set_token(r.json())

    def api(self, method: str, path: str, *, params: dict | None = None, body: Any = None,
            _retry: bool = True) -> Any:
        if not self.connected:
            raise SpotifyError("Spotify не подключён.")
        if time.time() >= self._token.get("expires_at", 0):
            self._refresh()
        try:
            r = requests.request(method, API + path, params=params, json=body, timeout=10,
                                 headers={"Authorization": f"Bearer {self._token['access_token']}"})
        except requests.RequestException:
            raise SpotifyError("Нет связи со Spotify.") from None
        if r.status_code == 401 and _retry:
            self._refresh()
            return self.api(method, path, params=params, body=body, _retry=False)
        if r.status_code == 429 and _retry:
            time.sleep(min(float(r.headers.get("Retry-After", 1)), 3))
            return self.api(method, path, params=params, body=body, _retry=False)
        if r.status_code == 404 and "/me/player" in path:
            raise NoDevice()
        if r.status_code == 403:
            reason = ""
            try:
                reason = r.json().get("error", {}).get("reason", "")
            except ValueError:
                pass
            if reason == "PREMIUM_REQUIRED":
                raise SpotifyError("Для управления воспроизведением нужен Spotify Premium.")
            raise SpotifyError("Spotify отклонил запрос.")
        if r.status_code >= 400:
            log.warning("Spotify API %s %s → HTTP %s", method, path, r.status_code)
            raise SpotifyError("Spotify не выполнил запрос.")
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def device_id(self, launch: bool = True) -> str | None:
        """Устройство для воспроизведения: активное, иначе Spotify на этом компьютере (запустить при необходимости)."""
        deadline = time.time() + 15
        launched = False
        while True:
            devices = (self.api("GET", "/me/player/devices") or {}).get("devices", [])
            active = next((d for d in devices if d.get("is_active")), None)
            local = next((d for d in devices if d.get("type") == "Computer"), None)
            chosen = active or local or (devices[0] if devices else None)
            if chosen:
                return chosen["id"]
            if not launch or time.time() > deadline:
                return None
            if not launched:
                os.startfile("spotify:")
                launched = True
            time.sleep(1.0)

    def player(self, method: str, path: str, *, params: dict | None = None, body: Any = None) -> Any:
        """Команда плееру; если нет активного устройства — выбрать/запустить Spotify и повторить."""
        try:
            return self.api(method, "/me/player" + path, params=params, body=body)
        except NoDevice:
            dev = self.device_id()
            if not dev:
                raise SpotifyError("Не нашёл запущенный Spotify.") from None
            return self.api(method, "/me/player" + path, params=dict(params or {}, device_id=dev), body=body)

    def restart_app(self) -> None:
        """Штатно закрыть приложение Spotify и запустить снова; дождаться, пока оно появится в сети."""
        import subprocess

        import psutil

        log.info("Spotify: приложение не принимает команды — перезапускаю")
        procs = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "spotify.exe"]
        if procs:
            subprocess.run(["taskkill", "/IM", "Spotify.exe"], capture_output=True, creationflags=0x08000000)
            _, alive = psutil.wait_procs(procs, timeout=5)
            for p in alive:
                try:
                    p.kill()
                except psutil.Error:
                    pass
        os.startfile("spotify:")
        deadline = time.time() + 25
        while time.time() < deadline:
            time.sleep(1.5)
            devices = (self.api("GET", "/me/player/devices") or {}).get("devices", [])
            if any(d.get("type") == "Computer" for d in devices):
                time.sleep(2.0)
                return
        raise SpotifyError("Spotify перезапустился, но не появился в сети.")

    def wake_device(self) -> str:
        """Передать воспроизведение на Spotify этого компьютера. Без этого Spotify бывает «активен» лишь на
        бумаге: команда play отвечает 204, а приложение её не получает и ничего не играет."""
        dev = self.device_id()
        if not dev:
            raise SpotifyError("Не нашёл запущенный Spotify.")
        self.api("PUT", "/me/player", body={"device_ids": [dev], "play": False})
        time.sleep(0.6)
        return dev

    def play(self, body: dict) -> dict:
        """Включить (uris или context_uri) и проверить, что действительно заиграло. Возвращает состояние плеера.
        Не заиграло после повторной попытки — SpotifyError (никаких «включаю», если ничего не играет)."""
        want = set(body.get("uris", [])[:1]) | ({body["context_uri"]} if body.get("context_uri") else set())
        for attempt in range(3):
            if attempt == 2:
                self.restart_app()
            dev = self.wake_device() if attempt else self.device_id()
            if not dev:
                raise SpotifyError("Не нашёл запущенный Spotify.")
            try:
                self.api("PUT", "/me/player/play", params={"device_id": dev}, body=body)
            except NoDevice:
                dev = self.wake_device()
                self.api("PUT", "/me/player/play", params={"device_id": dev}, body=body)
            deadline = time.time() + 3.5
            while time.time() < deadline:
                time.sleep(0.5)
                st = self.api("GET", "/me/player") or {}
                item = st.get("item") or {}
                ctx = (st.get("context") or {}).get("uri")
                started = st.get("is_playing") and (not want or item.get("uri") in want or ctx in want
                                                   or bool(body.get("uris") and item.get("uri") in body["uris"]))
                if started:
                    return st
            log.info("Spotify: команда принята, но не заиграло (попытка %d) — передаю воспроизведение", attempt + 1)
        raise SpotifyError("Spotify принимает команды, но ничего не заиграло даже после перезапуска приложения.")

    def search(self, query: str, kind: str = "track", limit: int = 5) -> list[dict]:
        data = self.api("GET", "/search", params={"q": query, "type": kind, "limit": limit}) or {}
        return [x for x in (data.get(kind + "s", {}) or {}).get("items", []) if x]

    def my_playlists(self) -> list[dict]:
        items, url_params = [], {"limit": 50, "offset": 0}
        for _ in range(4):
            page = self.api("GET", "/me/playlists", params=url_params) or {}
            items += [p for p in page.get("items", []) if p]
            if not page.get("next"):
                break
            url_params["offset"] += 50
        return items

    def current(self) -> dict | None:
        return self.api("GET", "/me/player/currently-playing", params={"additional_types": "track,episode"})

    def me(self) -> dict:
        return self.api("GET", "/me") or {}


class NoDevice(SpotifyError):
    pass


_instance: Spotify | None = None


def get() -> Spotify:
    global _instance
    if _instance is None:
        _instance = Spotify()
    return _instance
