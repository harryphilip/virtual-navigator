"""Outbound email. The only thing the site sends is a password-reset link.

The backend comes from the environment:

    MAIL_BACKEND=smtp     SMTP_HOST, SMTP_PORT (587 STARTTLS or 465 TLS),
                          SMTP_USER, SMTP_PASS, MAIL_FROM
    MAIL_BACKEND=console  log the message instead of sending it (dev, tests)
    MAIL_BACKEND=off      no email; the reset endpoints say so

With MAIL_BACKEND unset, smtp is assumed when SMTP_HOST is set, otherwise off.
Any provider with an SMTP endpoint works (Postmark, Resend, SES, Fastmail, a
Gmail app password); nothing here is provider-specific.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("vn.mail")
SENT = []            # console backend keeps what it "sent", so tests can read it


class MailNotConfigured(RuntimeError):
    pass


def backend():
    b = (os.environ.get("MAIL_BACKEND") or "").strip().lower()
    if b:
        return b
    return "smtp" if os.environ.get("SMTP_HOST") else "off"


def configured():
    return backend() in ("smtp", "console")


def send(to, subject, text):
    b = backend()
    sender = os.environ.get("MAIL_FROM") or "Virtual Navigator <no-reply@virtual-navigator.fly.dev>"
    if b == "console":
        SENT.append({"to": to, "from": sender, "subject": subject, "text": text})
        log.info("mail (console) to %s: %s\n%s", to, subject, text)
        return
    if b != "smtp":
        raise MailNotConfigured("email is not configured on this server")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or 587)
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
            if user:
                s.login(user, pw or "")
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            if user:
                s.login(user, pw or "")
            s.send_message(msg)
    log.info("mail sent to %s: %s", to, subject)
