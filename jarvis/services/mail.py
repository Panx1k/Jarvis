"""Почта через IMAP (чтение) и SMTP (отправка): Gmail, Mail.ru, Яндекс и любой ящик с IMAP.

Подключение — в .env: MAIL_ADDRESS и MAIL_APP_PASSWORD (пароль приложения, не обычный пароль от почты).
Второй и третий ящик — MAIL2_ADDRESS / MAIL2_APP_PASSWORD, MAIL3_… . Серверы определяются по домену адреса,
для своего сервера — MAIL_IMAP_HOST / MAIL_SMTP_HOST. Пароль не попадает в логи, интерфейс и ответы.
"""
from __future__ import annotations

import datetime as dt
import email
import html
import imaplib
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parseaddr, parsedate_to_datetime

from jarvis.config import env
from jarvis.utils.secrets import redact

log = logging.getLogger("jarvis.mail")

PROVIDERS = {
    ("gmail.com", "googlemail.com"): ("imap.gmail.com", "smtp.gmail.com", "Gmail"),
    ("mail.ru", "inbox.ru", "list.ru", "bk.ru", "internet.ru", "my.com"): ("imap.mail.ru", "smtp.mail.ru", "Mail.ru"),
    ("yandex.ru", "ya.ru", "yandex.com", "yandex.by", "yandex.kz", "narod.ru"): ("imap.yandex.ru", "smtp.yandex.ru",
                                                                                "Яндекс"),
    ("rambler.ru", "lenta.ru", "autorambler.ru"): ("imap.rambler.ru", "smtp.rambler.ru", "Rambler"),
}
PREFIXES = ("MAIL", "MAIL2", "MAIL3")
MAX_BODY = 8000


class MailError(RuntimeError):
    pass


@dataclass
class Account:
    prefix: str
    address: str
    imap_host: str
    smtp_host: str
    provider: str
    imap_port: int = 993
    smtp_port: int = 465

    @property
    def password(self) -> str:
        return env(f"{self.prefix}_APP_PASSWORD") or ""

    @property
    def label(self) -> str:
        return f"{self.provider} ({self.address})"


@dataclass
class Mail:
    uid: str
    account: str
    sender: str
    sender_address: str
    subject: str
    date: dt.datetime | None
    seen: bool = True
    to: str = ""
    body: str = ""
    attachments: list[str] = field(default_factory=list)
    message_id: str = ""
    references: str = ""
    folder: str = "INBOX"

    def when(self) -> str:
        if not self.date:
            return ""
        d = self.date.astimezone() if self.date.tzinfo else self.date
        today = dt.date.today()
        if d.date() == today:
            return f"сегодня в {d:%H:%M}"
        if d.date() == today - dt.timedelta(days=1):
            return f"вчера в {d:%H:%M}"
        return f"{d:%d.%m.%Y %H:%M}"

    def headline(self) -> str:
        who = self.sender or self.sender_address
        return f"от {who} — «{self.subject or 'без темы'}»" + (f" ({self.when()})" if self.date else "")


def accounts() -> list[Account]:
    found = []
    for prefix in PREFIXES:
        address = (env(f"{prefix}_ADDRESS") or "").strip()
        if not address or not env(f"{prefix}_APP_PASSWORD"):
            continue
        domain = address.rsplit("@", 1)[-1].lower()
        imap_host, smtp_host, provider = next((v for k, v in PROVIDERS.items() if domain in k),
                                              (f"imap.{domain}", f"smtp.{domain}", domain))
        found.append(Account(prefix, address, env(f"{prefix}_IMAP_HOST") or imap_host,
                             env(f"{prefix}_SMTP_HOST") or smtp_host, provider,
                             int(env(f"{prefix}_IMAP_PORT") or 993), int(env(f"{prefix}_SMTP_PORT") or 465)))
    return found


def pick(name: str | None = None) -> Account:
    items = accounts()
    if not items:
        raise MailError("Почта не подключена. Впишите MAIL_ADDRESS и MAIL_APP_PASSWORD в файл .env "
                        "(пароль приложения — как его получить, написано в README).")
    if not name:
        return items[0]
    n = name.lower()
    for a in items:
        if n in a.address.lower() or n in a.provider.lower() or (n.startswith("джи") and a.provider == "Gmail") \
                or (n.startswith(("мейл", "майл")) and a.provider == "Mail.ru") \
                or (n.startswith("яндекс") and a.provider == "Яндекс"):
            return a
    return items[0]


def decode(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except Exception:
        return str(value).strip()


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _payload_text(part) -> str:
    data = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def extract_body(msg) -> tuple[str, list[str]]:
    """Текст письма (обычный текст, иначе HTML без разметки) и названия вложений."""
    plain, rich, attachments = [], [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if filename or "attachment" in disposition:
            attachments.append(decode(filename) or "вложение")
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(_payload_text(part))
        elif ctype == "text/html":
            rich.append(html_to_text(_payload_text(part)))
    body = "\n".join(plain).strip() or "\n".join(rich).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body[:MAX_BODY] + ("\n…(письмо длиннее, показано начало)" if len(body) > MAX_BODY else ""), attachments


def _imap_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MailClient:
    def __init__(self, account: Account, imap_factory=None, smtp_factory=None):
        self.account = account
        self._imap_factory = imap_factory or (lambda host, port: imaplib.IMAP4_SSL(host, port, timeout=20))
        self._smtp_factory = smtp_factory or (lambda host, port: smtplib.SMTP_SSL(
            host, port, timeout=20, context=ssl.create_default_context()))

    def _connect(self):
        a = self.account
        try:
            imap = self._imap_factory(a.imap_host, a.imap_port)
            imap.login(a.address, a.password)
            return imap
        except imaplib.IMAP4.error as exc:
            raise MailError(self._auth_hint(str(exc))) from None
        except OSError as exc:
            raise MailError(f"Не удалось подключиться к почте {a.provider}: {exc.strerror or type(exc).__name__}.") \
                from None

    def _auth_hint(self, detail: str) -> str:
        log.warning("Почта %s: вход не выполнен (%s)", self.account.address, redact(detail)[:200])
        a = self.account
        hint = {"Gmail": "в Google: Аккаунт → Безопасность → Пароли приложений (нужна двухэтапная проверка)",
                "Mail.ru": "в Mail.ru: Настройки → Безопасность → Пароли для внешних приложений, и включите доступ по "
                           "IMAP в настройках почтовых программ",
                "Яндекс": "в Яндекс ID: Безопасность → Пароли приложений → Почта, и включите IMAP в настройках почты"}
        return (f"Почта {a.label} не пускает. Нужен пароль приложения, а не обычный пароль — создайте его "
                f"{hint.get(a.provider, 'в настройках безопасности почты')} и впишите в .env.")

    def _select(self, imap, folder: str) -> None:
        typ, _ = imap.select(_imap_quote(folder) if " " in folder else folder, readonly=False)
        if typ != "OK":
            raise MailError(f"Папка «{folder}» не найдена в почте.")

    def unread_count(self, folder: str = "INBOX") -> int:
        imap = self._connect()
        try:
            typ, data = imap.status(folder, "(UNSEEN)")
            m = re.search(rb"UNSEEN (\d+)", data[0] or b"") if typ == "OK" and data else None
            return int(m.group(1)) if m else 0
        finally:
            self._logout(imap)

    def search(self, *, unread: bool = False, sender: str = "", query: str = "", days: int = 0, limit: int = 10,
               folder: str = "INBOX") -> list[Mail]:
        """Письма по условиям, новые сверху. Русский текст ищется на сервере (UTF-8), а если сервер не умеет —
        среди последних писем по отправителю и теме."""
        imap = self._connect()
        try:
            self._select(imap, folder)
            criteria = ["UNSEEN"] if unread else []
            if days:
                since = (dt.date.today() - dt.timedelta(days=int(days))).strftime("%d-%b-%Y")
                criteria += ["SINCE", since]
            local_filter = []
            for key, value in (("FROM", sender), ("TEXT", query)):
                if not value:
                    continue
                if value.isascii():
                    criteria += [key, _imap_quote(value)]
                else:
                    local_filter.append((key, value))
            uids = self._uid_search(imap, criteria or ["ALL"], local_filter)
            uids = uids[-max(limit * 8 if local_filter else limit, limit):]
            mails = self._headers(imap, uids, folder)
            for key, value in local_filter:
                v = value.lower()
                if key == "FROM":
                    mails = [m for m in mails if v in (m.sender + " " + m.sender_address).lower()]
                elif not self._server_text:
                    mails = [m for m in mails if v in m.subject.lower()
                             or v in (m.sender + " " + m.sender_address).lower()]
            mails.sort(key=lambda m: m.date or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
            return mails[:limit]
        finally:
            self._logout(imap)

    def _uid_search(self, imap, criteria: list[str], local_filter: list[tuple[str, str]]) -> list[bytes]:
        self._server_text = False
        text = next((v for k, v in local_filter if k == "TEXT"), None)
        if text:
            try:
                imap.literal = text.encode("utf-8")
                typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", *criteria, "TEXT")
                if typ == "OK":
                    self._server_text = True
                    return (data[0] or b"").split()
            except imaplib.IMAP4.error:
                log.info("Почтовый сервер не ищет по русскому тексту — ищу по заголовкам")
            finally:
                imap.literal = None
            criteria = criteria or ["ALL"]
        typ, data = imap.uid("SEARCH", *criteria)
        if typ != "OK":
            raise MailError("Почтовый сервер не выполнил поиск.")
        return (data[0] or b"").split()

    def _headers(self, imap, uids: list[bytes], folder: str) -> list[Mail]:
        if not uids:
            return []
        typ, data = imap.uid("FETCH", b",".join(uids).decode(),
                             "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
        if typ != "OK":
            raise MailError("Не удалось получить список писем.")
        mails = []
        for item in data:
            if not isinstance(item, tuple):
                continue
            meta, raw = item[0].decode(errors="replace"), item[1]
            uid = re.search(r"UID (\d+)", meta)
            msg = email.message_from_bytes(raw)
            mails.append(self._mail(uid.group(1) if uid else "", msg, "\\Seen" in meta, folder))
        return mails

    def _mail(self, uid: str, msg, seen: bool, folder: str) -> Mail:
        name, address = parseaddr(decode(msg.get("From")))
        try:
            date = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        except (TypeError, ValueError):
            date = None
        return Mail(uid, self.account.address, name, address, decode(msg.get("Subject")), date, seen,
                    decode(msg.get("To")), message_id=msg.get("Message-ID", ""),
                    references=msg.get("References", ""), folder=folder)

    def read(self, uid: str, folder: str = "INBOX", mark_seen: bool = True) -> Mail:
        imap = self._connect()
        try:
            self._select(imap, folder)
            typ, data = imap.uid("FETCH", str(uid), "(FLAGS BODY.PEEK[])")
            item = next((x for x in data or [] if isinstance(x, tuple)), None)
            if typ != "OK" or item is None:
                raise MailError("Письмо не найдено — возможно, его удалили.")
            msg = email.message_from_bytes(item[1])
            mail = self._mail(str(uid), msg, True, folder)
            mail.body, mail.attachments = extract_body(msg)
            if mark_seen:
                imap.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")
            return mail
        finally:
            self._logout(imap)

    def send(self, to: str, subject: str, body: str, reply_to: Mail | None = None) -> None:
        a = self.account
        msg = EmailMessage()
        msg["From"] = formataddr(("", a.address))
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to is not None and reply_to.message_id:
            msg["In-Reply-To"] = reply_to.message_id
            msg["References"] = (reply_to.references + " " + reply_to.message_id).strip()
        msg.set_content(body)
        try:
            smtp = self._smtp_factory(a.smtp_host, a.smtp_port)
            try:
                smtp.login(a.address, a.password)
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
        except smtplib.SMTPAuthenticationError as exc:
            raise MailError(self._auth_hint(str(exc))) from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"Письмо не отправлено: {redact(str(exc))[:160]}") from None

    @staticmethod
    def _logout(imap) -> None:
        try:
            imap.logout()
        except Exception:
            pass


def valid_address(text: str) -> bool:
    return bool(re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}", (text or "").strip()))


def addresses(text: str) -> list[str]:
    return [addr for _, addr in getaddresses([text or ""]) if valid_address(addr)]
