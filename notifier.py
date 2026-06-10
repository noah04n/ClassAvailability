"""Email notifier — Gmail SMTP over STARTTLS (port 587).

Uses smtplib so there are no third-party deps. Sender authenticates with a
Google App Password (16-char code generated at myaccount.google.com/apppasswords
with 2FA enabled), NOT the regular account password.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from config import Settings


class EmailError(Exception):
    pass


def _build_message(sender: str, recipient: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)
    return msg


def send_email(settings: Settings, subject: str, body: str,
               recipient: str | None = None, timeout: float = 20.0) -> None:
    """Send an email via SMTP. If `recipient` is None, fall back to
    settings.recipient_email (the legacy default)."""
    if not settings.sender_email or not settings.sender_app_password:
        raise EmailError("Sender email and app password are not configured.")
    to_addr = (recipient or settings.recipient_email).strip()
    if not to_addr:
        raise EmailError("Recipient email is not configured.")

    msg = _build_message(settings.sender_email, to_addr, subject, body)
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(settings.sender_email, settings.sender_app_password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(
            "Gmail rejected the login. Double-check the sender address and that "
            "you're using a 16-character App Password (not your Google password). "
            f"Server said: {exc.smtp_error.decode('utf-8', 'replace') if exc.smtp_error else exc}"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"SMTP error: {exc}") from exc


def send_test(settings: Settings) -> None:
    send_email(
        settings,
        subject="ClassAvailability test email",
        body=(
            "This is a test email from your ClassAvailability tracker.\n\n"
            "If you're reading this, your SMTP settings are working correctly."
        ),
    )


def format_opening(section_label: str, course_code: str, course_title: str,
                   term: str, open_seats: int, note: str) -> tuple[str, str]:
    """Build (subject, body) for a 'seat opened' notification."""
    subject = f"[ClassAvailability] {course_code} {section_label} has {open_seats} open seat(s)"
    body_lines = [
        f"{course_code} — {course_title}",
        f"Section: {section_label}",
        f"Term: {term}",
        f"Open seats: {open_seats}",
    ]
    if note:
        body_lines.append(f"Note: {note}")
    body_lines.append("")
    body_lines.append(
        "Register now at https://horizon.mcgill.ca/pban1/bwskfreg.P_AltPin"
    )
    return subject, "\n".join(body_lines)


def format_waitlist_opening(section_label: str, course_code: str, course_title: str,
                            term: str, waitlist_seats: int, waitlist_capacity: int,
                            note: str) -> tuple[str, str]:
    """Build (subject, body) for a 'waitlist spot available' notification."""
    subject = f"[ClassAvailability] {course_code} {section_label} has a waitlist spot available"
    seats = (f"{waitlist_seats} of {waitlist_capacity}"
             if waitlist_capacity else str(waitlist_seats))
    body_lines = [
        f"{course_code} — {course_title}",
        f"Section: {section_label}",
        f"Term: {term}",
        f"Waitlist seats available: {seats}",
        "",
        "The section is full, but there is room on its waitlist.",
    ]
    if note:
        body_lines.append(f"Note: {note}")
    body_lines.append("")
    body_lines.append(
        "Add yourself to the waitlist at "
        "https://horizon.mcgill.ca/pban1/bwskfreg.P_AltPin"
    )
    return subject, "\n".join(body_lines)
