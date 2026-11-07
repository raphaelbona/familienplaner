"""Sends transactional email (currently just password-reset links) via SMTP.

Self-hosted families without an SMTP relay configured still get a working
flow: the reset link is logged instead of emailed (see SELF_HOSTING.md).
"""

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import Settings

logger = logging.getLogger("email")


def send_password_reset_email(to_email: str, reset_link: str, settings: Settings) -> None:
    if not settings.smtp_host:
        logger.warning("SMTP nicht konfiguriert - Passwort-Reset-Link für %s: %s", to_email, reset_link)
        return

    message = EmailMessage()
    message["Subject"] = "Familienplaner: Passwort zurücksetzen"
    message["From"] = settings.smtp_from or "noreply@familienplaner.local"
    message["To"] = to_email
    message.set_content(
        "Klicke auf den folgenden Link, um dein Passwort zurückzusetzen "
        f"(gültig für 30 Minuten):\n\n{reset_link}\n\n"
        "Falls du das nicht angefordert hast, kannst du diese E-Mail ignorieren."
    )

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Passwort-Reset-E-Mail an %s konnte nicht gesendet werden: %s", to_email, exc)
