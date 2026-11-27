"""v1.14 mail link: invoice attachments from an IMAP mailbox, and notification mails over SMTP.

The app password is kept in the Windows credential manager (keyring), never in settings.json.
Mails are only read (BODY.PEEK: they stay unread) and never deleted or moved."""

import email
import hashlib
import imaplib
import json
import re
import smtplib
import ssl
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, parsedate_to_datetime
from pathlib import Path

KEYRING_SERVICE = "InvoiceRenamer-Mail"
ATTACHMENT_EXTENSIONS = {".pdf", ".xml", ".jpg", ".jpeg", ".png"}
MAX_MESSAGES = 400
MAX_ATTACHMENT = 25 * 1024 * 1024

# (IMAP host, IMAP port, SMTP host, SMTP port, note)
MAIL_PRESETS = {
    "Gmail": ("imap.gmail.com", 993, "smtp.gmail.com", 465,
              "Google 계정 → 보안 → 2단계 인증을 켠 뒤 ‘앱 비밀번호’를 만들어 넣어요 (평소 비밀번호는 안 돼요)."),
    "한메일 (Daum)": ("imap.daum.net", 993, "smtp.daum.net", 465,
                   "Daum 메일 → 환경설정 → IMAP/SMTP 사용을 켜요. 2단계 인증이면 앱 비밀번호를 넣어요."),
    "GMX": ("imap.gmx.net", 993, "mail.gmx.net", 587, "GMX → E-Mail-Einstellungen → POP3/IMAP Abruf erlauben."),
    "WEB.DE": ("imap.web.de", 993, "smtp.web.de", 587, "WEB.DE → Einstellungen → POP3/IMAP Abruf erlauben."),
    "직접 입력": ("", 993, "", 465, "메일 회사가 알려 준 IMAP·SMTP 서버 주소를 넣어요."),
}


def account_settings(settings):
    """The saved account (no password): {preset, imap_host, imap_port, smtp_host, smtp_port, user, sender, folder}."""
    saved = settings.get("mail_account")
    saved = saved if isinstance(saved, dict) else {}
    result = {"preset": "Gmail", "imap_host": "", "imap_port": 993, "smtp_host": "", "smtp_port": 465, "user": "",
              "sender": "", "folder": "INBOX"}
    for field, default in result.items():
        value = saved.get(field, default)
        if isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = default
        result[field] = value if isinstance(value, type(default)) else default
    return result


def configured(account):
    return bool(account.get("user") and account.get("imap_host"))


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as error:
        raise RuntimeError("비밀번호 보관함(keyring)을 불러오지 못했어요.") from error
    return keyring, KeyringError


def save_password(user, password):
    keyring, KeyringError = _keyring()
    try:
        keyring.set_password(KEYRING_SERVICE, user, password)
    except (KeyringError, RuntimeError, OSError) as error:
        raise RuntimeError(f"Windows 자격 증명 관리자에 저장하지 못했어요: {error}") from error


def load_password(user):
    """The stored app password, or "" when none is stored."""
    keyring, KeyringError = _keyring()
    try:
        return keyring.get_password(KEYRING_SERVICE, user) or ""
    except (KeyringError, RuntimeError, OSError) as error:
        raise RuntimeError(f"Windows 자격 증명 관리자를 읽지 못했어요: {error}") from error


def delete_password(user):
    keyring, KeyringError = _keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE, user)
    except (KeyringError, RuntimeError, OSError):
        pass


def decoded(value):
    try:
        return str(make_header(decode_header(value or "")))
    except (ValueError, LookupError, UnicodeError):
        return str(value or "")


def attachment_key(message_id, filename, payload):
    """Same mail attachment → same key (also when the mail is in two folders)."""
    return hashlib.sha256(f"{message_id}|{filename}|".encode("utf-8", "replace") + payload).hexdigest()


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", Path(name).name).strip(" .")
    return name[:150] or "anhang"


def imap_connect(account, password, timeout=30):
    connection = imaplib.IMAP4_SSL(account["imap_host"], account["imap_port"], ssl_context=ssl.create_default_context(),
                                   timeout=timeout)
    try:
        connection.login(account["user"], password)
    except imaplib.IMAP4.error as error:
        connection.logout()
        raise RuntimeError(f"메일 로그인 실패: 주소·앱 비밀번호를 확인하세요 ({decoded(str(error))})") from error
    return connection


def fetch_attachments(account, password, days=30, seen=(), progress=None, connect=None):
    """Invoice-like attachments (PDF, XML, JPG, PNG) of the last days, newest first, without marking mails as read.
    Returns (found [{key, filename, data, sender, subject, day}], skipped count). seen: attachment keys taken before."""
    seen = set(seen)
    connection = (connect or imap_connect)(account, password)
    found, skipped = [], 0
    try:
        status, _ = connection.select(account.get("folder") or "INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"메일함 ‘{account.get('folder')}’을 열지 못했어요.")
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        status, data = connection.uid("SEARCH", None, f"(SINCE {since})")
        if status != "OK":
            raise RuntimeError("메일을 검색하지 못했어요.")
        uids = (data[0] or b"").split()[-MAX_MESSAGES:]
        for position, uid in enumerate(reversed(uids), 1):
            if progress:
                progress(position, len(uids), f"메일 {position}/{len(uids)}")
            status, parts = connection.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((part[1] for part in parts or [] if isinstance(part, tuple) and len(part) > 1), None)
            if status != "OK" or not raw:
                continue
            message = email.message_from_bytes(raw)
            message_id = message.get("Message-ID", "") or uid.decode()
            try:
                sent = parsedate_to_datetime(message.get("Date")).date().isoformat()
            except (TypeError, ValueError):
                sent = ""
            for part in message.walk():
                filename = decoded(part.get_filename() or "")
                if not filename or Path(filename).suffix.lower() not in ATTACHMENT_EXTENSIONS:
                    continue
                payload = part.get_payload(decode=True) or b""
                if not payload or len(payload) > MAX_ATTACHMENT:
                    skipped += 1
                    continue
                key = attachment_key(message_id, filename, payload)
                if key in seen:
                    continue
                seen.add(key)
                found.append({"key": key, "filename": safe_filename(filename), "data": payload,
                              "sender": decoded(message.get("From", "")), "subject": decoded(message.get("Subject", "")),
                              "day": sent})
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
    return found, skipped


def smtp_send(account, password, to, subject, body, attachments=(), connect=None):
    """Send one plain-text mail. attachments: [(filename, bytes)]. The account address is the sender."""
    message = EmailMessage()
    message["From"] = formataddr((account.get("sender") or "", account["user"]))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    for filename, data in attachments:
        subtype = "zip" if filename.lower().endswith(".zip") else "pdf" if filename.lower().endswith(".pdf") else "octet-stream"
        message.add_attachment(data, maintype="application", subtype=subtype, filename=filename)
    if connect is None:
        context = ssl.create_default_context()
        if account["smtp_port"] == 465:
            server = smtplib.SMTP_SSL(account["smtp_host"], 465, context=context, timeout=60)
        else:
            server = smtplib.SMTP(account["smtp_host"], account["smtp_port"], timeout=60)
            server.starttls(context=context)
    else:
        server = connect(account)
    try:
        server.login(account["user"], password)
        server.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise RuntimeError("메일 보내기 로그인 실패: 앱 비밀번호를 확인하세요.") from error
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass


def load_seen(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember_seen(path, entries):
    """entries: {attachment key: {"date", "file", "action"}} (taken in or hidden)."""
    data = load_seen(path)
    data.update(entries)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)
