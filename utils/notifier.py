"""
Admin notifications.

Sends a Telegram message to a configured recipient every time a feedback
record is created or an existing identity is re-submitted (i.e. "changed").

Kept fully out of the FSM handler so report.py only needs a single call.

Configuration (env / .env):
    NOTIFY_CHAT_ID   numeric chat id of the admin (RECOMMENDED). The admin must
                     have pressed Start in the bot at least once. Get it by
                     sending /myid to the bot.
    NOTIFY_USERNAME  public @username fallback (default: @igotab). Works only if
                     the target is a public channel/supergroup; a normal user
                     cannot reliably be reached by @username via the Bot API, so
                     prefer NOTIFY_CHAT_ID.

"Changed" vs "new" is decided by whether the record's identity (ID1) has been
seen before. Seen identities are persisted to NOTIFY_SEEN_PATH so a restart does
not turn every record back into "new".
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)

NOTIFY_CHAT_ID = os.getenv("NOTIFY_CHAT_ID", "5810251318").strip()
NOTIFY_USERNAME = os.getenv("NOTIFY_USERNAME", "@igotab").strip()
NOTIFY_SEEN_PATH = os.getenv("NOTIFY_SEEN_PATH", "./notified_ids.json")

_lock = asyncio.Lock()
_seen: Set[str] | None = None  # lazily loaded identity cache


def _target() -> str | int | None:
    """Resolve the recipient: numeric chat id wins, else a public @username."""
    if NOTIFY_CHAT_ID:
        try:
            return int(NOTIFY_CHAT_ID)
        except ValueError:
            return NOTIFY_CHAT_ID  # allow @channelusername stored here too
    if NOTIFY_USERNAME:
        return NOTIFY_USERNAME if NOTIFY_USERNAME.startswith("@") else "@" + NOTIFY_USERNAME
    return None


def _load_seen() -> Set[str]:
    global _seen
    if _seen is not None:
        return _seen
    seen: Set[str] = set()
    try:
        p = Path(NOTIFY_SEEN_PATH)
        if p.exists():
            seen = set(json.loads(p.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.error("Could not read notified-ids store: %s", exc)
    _seen = seen
    return _seen


def _save_seen(seen: Set[str]) -> None:
    try:
        Path(NOTIFY_SEEN_PATH).write_text(
            json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("Could not persist notified-ids store: %s", exc)


def _identity(data: dict) -> str:
    return (data.get("id1", "") or "").strip().lower()


def _e(value: object) -> str:
    """HTML-escape a value for safe insertion into the message."""
    return html.escape(str(value)) if value not in (None, "") else "—"


def _build_message(data: dict, is_new: bool) -> str:
    head = "🆕 <b>Новий запис</b>" if is_new else "✏️ <b>Оновлений запис</b>"
    return (
        f"{head}\n\n"
        f"🆔 ID1: {_e(data.get('id1'))}\n"
        f"🏢 ID2: {_e(data.get('id2'))}\n"
        f"🕒 Час/тривалість: {_e(data.get('id3'))}\n"
        f"🌐 Канал: {_e(data.get('id4'))}\n"
        f"📷 Медіа: {_e(data.get('media'))}\n"
        f"📞 Контакт: {_e(data.get('contact'))}\n"
        f"🔗 Deep-link: {_e(data.get('deeplink'))}\n"
        f"🕓 Записано: {_e(data.get('timestamp'))}\n"
        f"🔒 Хеш: <code>{_e(data.get('user_hash'))}</code>"
    )


async def notify(bot, data: dict, *, saved: bool = True) -> None:
    """Send an admin notification for a saved record. Never raises."""
    if not saved:
        return
    target = _target()
    if target is None:
        return

    async with _lock:
        seen = _load_seen()
        ident = _identity(data)
        is_new = ident not in seen if ident else True
        if ident:
            seen.add(ident)
            _save_seen(seen)

    try:
        await bot.send_message(target, _build_message(data, is_new))
        logger.info("Admin notification sent (new=%s).", is_new)
    except Exception as exc:
        logger.error("Admin notification failed (target=%s): %s", target, exc)
