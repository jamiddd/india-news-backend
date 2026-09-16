"""Out-of-band developer email alerts, independent of FCM/device tokens.

Added alongside the admin_alerts FCM topic (see admin_notify.py) after a
breaking review fired and reached nobody: the developer is frequently signed
out on debug installs, so no device token exists to push to. Email doesn't
depend on the phone, the app, or FCM being healthy at all — it's the
fallback channel that stays reachable even if both of those fail.

Plain Gmail SMTP (app password), deliberately not the Brevo account planned
for real user-facing email — this is dev-only, so stdlib smtplib is enough
and adds no new dependency.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import socket
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


class _IPv4SMTP(smtplib.SMTP):
    """smtplib.SMTP, but forced onto an IPv4 socket.

    getaddrinfo() for smtp.gmail.com returns both an AAAA and an A record,
    and the stdlib tries them in OS-preferred order — on a droplet with no
    IPv6 route configured (the DigitalOcean default), that means the first
    connect attempt fails immediately with OSError: [Errno 101] Network is
    unreachable, before IPv4 is ever tried. self._host stays the DNS name
    (only the socket's address family is pinned), so starttls()'s
    certificate hostname check against smtp.gmail.com is unaffected.
    """

    def _get_socket(self, host, port, timeout):
        addrinfo = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        family, socktype, proto, _canonname, sockaddr = addrinfo[0]
        sock = socket.socket(family, socktype, proto)
        if timeout is not None and timeout != socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        sock.connect(sockaddr)
        return sock


def _send_sync(subject: str, body: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_USER
    msg["To"] = settings.ADMIN_ALERT_EMAIL_TO
    msg.set_content(body)

    # Short timeout so a hung SMTP handshake can never wedge the poll cycle.
    with _IPv4SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(msg)
    return True


async def send_admin_email(subject: str, body: str) -> bool:
    """Best-effort developer alert email. Returns whether it was sent.

    No-ops (returns False) when SMTP_USER/SMTP_PASSWORD/ADMIN_ALERT_EMAIL_TO
    aren't all configured, so this is inert on any machine without the
    secrets. Never raises — a notification failure must never touch the
    caller's own transaction, same posture as admin_notify._push_to_admin.
    """
    if not (settings.SMTP_USER and settings.SMTP_PASSWORD and settings.ADMIN_ALERT_EMAIL_TO):
        return False

    try:
        return await asyncio.to_thread(_send_sync, subject, body)
    except Exception as e:
        # Log the exception type only — never the repr, which can echo the
        # SMTP password back through certain smtplib auth failures.
        logger.warning(f"[AdminEmail] send failed: {type(e).__name__}")
        return False
