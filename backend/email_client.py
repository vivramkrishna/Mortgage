"""SMTP delivery for OTP verification codes.

Real send only — this repo intentionally does not mock/echo the OTP in the
chat response (see backend/config.DEBUG_EXPOSE_OTP for the local-dev-only
alternative used by scripts/test_mcp.py).
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from backend import config

logger = logging.getLogger(__name__)


def send_otp_email(to_email: str, otp: str) -> None:
    if not config.SMTP_HOST:
        raise RuntimeError(
            "SMTP is not configured — set SMTP_HOST, SMTP_USERNAME, "
            "SMTP_PASSWORD and SMTP_FROM_EMAIL in .env before requesting an OTP."
        )

    message = EmailMessage()
    message["Subject"] = "Your Lloyds verification code"
    message["From"] = config.SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        f"Your Lloyds verification code is: {otp}\n\n"
        "This code expires in 5 minutes. If you didn't request this, you can "
        "safely ignore this email."
    )

    logger.info("Connecting to SMTP %s:%s to send OTP to %s", config.SMTP_HOST, config.SMTP_PORT, to_email)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as smtp:
            if config.SMTP_USE_TLS:
                smtp.starttls()
            if config.SMTP_USERNAME:
                smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            smtp.send_message(message)
    except Exception:
        logger.exception("SMTP send FAILED for %s", to_email)
        raise

    logger.info("OTP email SENT successfully to %s", to_email)
