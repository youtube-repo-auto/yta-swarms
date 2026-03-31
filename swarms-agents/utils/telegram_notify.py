"""
Telegram Notification Utility
==============================
Sends notifications to a Telegram chat via the Bot API.
Uses httpx (already in requirements) — no extra dependencies needed.
"""

import os
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_notification(
    title: str,
    youtube_url: str,
    *,
    extra_text: str = "",
) -> bool:
    """
    Send a Telegram message about a published video.

    Returns True on success, False on failure (never raises).
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping notification")
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (
        f"🎬 *Video Published!*\n\n"
        f"*Title:* {_escape_md(title)}\n"
        f"*URL:* {youtube_url}\n"
        f"*Time:* {timestamp}"
    )
    if extra_text:
        text += f"\n\n{_escape_md(extra_text)}"

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                TELEGRAM_API.format(token=token),
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
            )
            resp.raise_for_status()
        logger.info("Telegram notification sent for: %s", title)
        return True
    except Exception:
        logger.exception("Failed to send Telegram notification")
        return False


def _escape_md(text: str) -> str:
    """Escape Markdown V1 special characters."""
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text
