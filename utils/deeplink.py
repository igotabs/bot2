"""
Deep-link mechanism.

A Telegram deep link looks like ``https://t.me/<bot>?start=<payload>``. When a
user opens it, the bot receives ``/start <payload>``. We capture that payload
per user and later stamp it onto their report row so every submission can be
traced back to the campaign / source link it came from.

The payload is remembered per Telegram user id (the report FSM clears its own
state between steps, so we keep this in a small side-store that also survives a
restart via a JSON file).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Telegram allows only A-Z a-z 0-9 _ - in a start payload (max 64 chars).
_VALID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STORE_PATH = Path("./deeplinks.json")

# user_id -> {"payload": str, "ts": iso}
_payloads: Optional[Dict[str, dict]] = None


def is_valid(payload: str) -> bool:
    return bool(payload) and bool(_VALID.match(payload))


def _load() -> Dict[str, dict]:
    global _payloads
    if _payloads is not None:
        return _payloads
    data: Dict[str, dict] = {}
    try:
        if _STORE_PATH.exists():
            data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Could not read deep-link store: %s", exc)
    _payloads = data
    return _payloads


def _save(data: Dict[str, dict]) -> None:
    try:
        _STORE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("Could not persist deep-link store: %s", exc)


def set_payload(user_id: int, payload: str) -> None:
    """Remember the deep-link payload a user arrived with (validated)."""
    payload = (payload or "").strip()
    if not is_valid(payload):
        return
    data = _load()
    data[str(user_id)] = {"payload": payload, "ts": datetime.now().isoformat(timespec="seconds")}
    _save(data)
    logger.info("Deep-link payload stored for user (payload=%s).", payload)


def get_payload(user_id: int) -> str:
    """Return the last deep-link payload for a user, or '' if none."""
    entry = _load().get(str(user_id))
    return entry.get("payload", "") if entry else ""


def build_link(bot_username: str, payload: str) -> str:
    """Build a shareable deep link for a given payload."""
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={payload}"
