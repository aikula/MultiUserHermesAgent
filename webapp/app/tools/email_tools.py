"""Backend email tool — IMAP/SMTP operations without exposing credentials to LLM.

The LLM never sees passwords. It requests actions via structured payloads,
and this module executes them using credentials from the database.
"""
import email
import email.header
import imaplib
import smtplib
import ssl
from email.mime.text import MIMEText

from .db import get_db
from ..secrets_store import decrypt


def _get_creds(uid: str) -> dict | None:
    """Retrieve email credentials from DB (decrypting password). Returns None if not configured."""
    db = get_db()
    row = db.execute(
        "SELECT email_imap_host, email_imap_port, email_smtp_host, email_smtp_port, "
        "email_login, email_password FROM users WHERE uid=?",
        (uid,),
    ).fetchone()
    if not row or not row["email_imap_host"] or not row["email_password"]:
        return None
    creds = dict(row)
    creds["email_password"] = decrypt(creds["email_password"], uid)
    return creds


def _decode_header(raw: str) -> str:
    """Decode RFC 2047 encoded header."""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return "".join(decoded)


def check_connection(uid: str) -> dict:
    """Test IMAP connection. Returns {ok, message}."""
    creds = _get_creds(uid)
    if not creds:
        return {"ok": False, "message": "Email not configured"}
    try:
        mail = imaplib.IMAP4_SSL(creds["email_imap_host"], creds["email_imap_port"])
        mail.login(creds["email_login"], creds["email_password"])
        mail.logout()
        return {"ok": True, "message": "Connection successful"}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "message": f"IMAP error: {e}"}
    except Exception as e:
        return {"ok": False, "message": f"Connection error: {e}"}


def list_folders(uid: str) -> dict:
    """List IMAP folders. Returns {ok, folders: [...]} or {ok:false, message}."""
    creds = _get_creds(uid)
    if not creds:
        return {"ok": False, "message": "Email not configured"}
    try:
        mail = imaplib.IMAP4_SSL(creds["email_imap_host"], creds["email_imap_port"])
        mail.login(creds["email_login"], creds["email_password"])
        status, folders = mail.list()
        mail.logout()
        if status != "OK":
            return {"ok": False, "message": "Failed to list folders"}
        result = []
        for f in folders:
            if isinstance(f, bytes):
                parts = f.decode("utf-8", errors="replace").split(' "/" ')
                if len(parts) == 2:
                    result.append(parts[1].strip('"'))
        return {"ok": True, "folders": result}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def search_emails(uid: str, folder: str = "INBOX", query: str = "ALL", limit: int = 20) -> dict:
    """Search emails and return summaries (no body). Returns {ok, emails: [...]}."""
    creds = _get_creds(uid)
    if not creds:
        return {"ok": False, "message": "Email not configured"}
    try:
        mail = imaplib.IMAP4_SSL(creds["email_imap_host"], creds["email_imap_port"])
        mail.login(creds["email_login"], creds["email_password"])
        status, _ = mail.select(folder, readonly=True)
        if status != "OK":
            mail.logout()
            return {"ok": False, "message": f"Cannot select folder: {folder}"}

        status, msg_ids = mail.search(None, query)
        if status != "OK" or not msg_ids[0]:
            mail.logout()
            return {"ok": True, "emails": []}

        ids = msg_ids[0].split()
        # Get last N
        ids = ids[-limit:] if len(ids) > limit else ids

        emails = []
        for mid in ids:
            status, msg_data = mail.fetch(mid, "(FLAGS BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
            if status != "OK":
                continue
            header_data = msg_data[0][1].decode("utf-8", errors="replace") if msg_data[0][1] else ""
            msg = email.message_from_string(header_data)
            emails.append({
                "id": mid.decode(),
                "from": _decode_header(msg.get("From", "")),
                "to": _decode_header(msg.get("To", "")),
                "subject": _decode_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
            })

        mail.logout()
        return {"ok": True, "emails": emails}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def read_email(uid: str, folder: str, msg_id: str) -> dict:
    """Read full email by ID. Returns {ok, email: {from, to, subject, date, body}}."""
    creds = _get_creds(uid)
    if not creds:
        return {"ok": False, "message": "Email not configured"}
    try:
        mail = imaplib.IMAP4_SSL(creds["email_imap_host"], creds["email_imap_port"])
        mail.login(creds["email_login"], creds["email_password"])
        status, _ = mail.select(folder, readonly=True)
        if status != "OK":
            mail.logout()
            return {"ok": False, "message": f"Cannot select folder: {folder}"}

        status, msg_data = mail.fetch(msg_id.encode(), "(RFC822)")
        mail.logout()
        if status != "OK" or not msg_data or not msg_data[0]:
            return {"ok": False, "message": "Email not found"}

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        # Extract body
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not part.get("Content-Disposition", "").startswith("attachment"):
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

        return {
            "ok": True,
            "email": {
                "from": _decode_header(msg.get("From", "")),
                "to": _decode_header(msg.get("To", "")),
                "subject": _decode_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": body,
            },
        }
    except Exception as e:
        return {"ok": False, "message": str(e)}


def send_email(uid: str, to: str, subject: str, body: str) -> dict:
    """Send email via SMTP. Returns {ok, message}."""
    creds = _get_creds(uid)
    if not creds:
        return {"ok": False, "message": "Email not configured"}
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = creds["email_login"]
        msg["To"] = to
        msg["Subject"] = subject

        context = ssl.create_default_context()
        with smtplib.SMTP(creds["email_smtp_host"], creds["email_smtp_port"]) as server:
            server.starttls(context=context)
            server.login(creds["email_login"], creds["email_password"])
            server.send_message(msg)

        return {"ok": True, "message": f"Email sent to {to}"}
    except Exception as e:
        return {"ok": False, "message": f"Send error: {e}"}
