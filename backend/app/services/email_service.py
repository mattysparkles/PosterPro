from __future__ import annotations

import smtplib
import sys
from email.message import EmailMessage
from urllib.parse import urlencode

from app.core.config import settings


class EmailDeliveryError(RuntimeError):
    """Raised when email delivery fails."""


def smtp_configured() -> bool:
    if "pytest" in sys.modules:
        return False
    return bool(
        settings.smtp_host
        and settings.smtp_port
        and settings.smtp_from_email
        and settings.app_base_url
        and (not settings.smtp_username or settings.smtp_password)
    )


def build_password_reset_link(token: str) -> str:
    base_url = (settings.app_base_url or "").rstrip("/")
    query = urlencode({"token": token})
    return f"{base_url}/reset-password?{query}"


def send_password_reset_email(recipient_email: str, token: str) -> str:
    if not smtp_configured():
        raise EmailDeliveryError("SMTP delivery is not configured")

    reset_link = build_password_reset_link(token)
    from_email = settings.smtp_from_email or ""
    from_name = (settings.smtp_from_name or settings.app_name or "PosterPro").strip()

    message = EmailMessage()
    message["To"] = recipient_email
    message["From"] = f"{from_name} <{from_email}>"
    message["Subject"] = "Reset your PosterPro password"
    message.set_content(
        "\n".join(
            [
                "A password reset was requested for your PosterPro account.",
                "",
                "Use this link to choose a new password:",
                reset_link,
                "",
                "If you did not request this, you can ignore this email.",
            ]
        )
    )

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            if settings.smtp_use_tls:
                smtp.starttls()
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001
        raise EmailDeliveryError(str(exc)) from exc

    return reset_link
