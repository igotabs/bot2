"""
FSM workflow for structured field-data collection.

Flow:
    /report -> step_id1 (target identifier) -> step_id2 (address)
            -> step_id3 (schedule) -> step_id4 (location)
            -> step_media (optional) -> step_contact (optional)
            -> save to Excel -> post_menu ("add another" / "finish")

Privacy & anti-abuse:
    * user_id is only ever stored as a truncated SHA-256 hash.
    * Message *content* is never written to the logs.
    * Rate limit: one session per user every RATE_LIMIT_MINUTES.
    * Duplicate guard: same (user_hash + id1 + id2) within 24h is rejected
      WITHOUT dropping the current FSM session.
    * Excel writes are serialized with an asyncio.Lock; on failure the row is
      pushed to a JSON retry queue instead of being lost.

All user-facing strings come from CONFIG (external file); the code only ever
references KEY names plus a safe fallback.
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from utils.config_loader import CONFIG
from utils import deeplink, excel_report, notifier, sharepoint

router = Router()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration (via environment / .env)
# --------------------------------------------------------------------------- #
RATE_LIMIT_MINUTES = int(os.getenv("RATE_LIMIT_MINUTES", "15"))
DUPLICATE_WINDOW_HOURS = int(os.getenv("DUPLICATE_WINDOW_HOURS", "24"))
EXCEL_PATH = os.getenv("EXCEL_PATH", "C:/Users/oleg/OneDrive - ФІЗИЧНА ОСОБА-ПІДПРИЄМЕЦЬ СТЕБЛЄВ ОЛЕГ ІГОРОВИЧ/night/reports.xlsx")
QUEUE_PATH = os.getenv("QUEUE_PATH", "./failed_reports.json")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "./media"))
SAVE_MEDIA_FILES = os.getenv("SAVE_MEDIA_FILES", "1").strip() not in ("0", "false", "False", "")

EXCEL_COLUMNS = ["Timestamp", "ID1", "ID2", "ID3", "ID4", "Media_Ref", "Contact", "User_Hash"]

# Validation thresholds
MIN_LEN_ID1 = 3
MIN_LEN_ID2 = 5
MIN_LEN_ID3 = 10
MIN_LEN_ID4 = 10

# Address markers expected in ID2 (street, building, district, etc.)
ID2_MARKERS = (
    "вул", "просп", "бул", "пров", "пл", "буд", "під", "жк", "район",
    "кв", "шосе", "наб", "будинок", "квартира", "поверх", "корпус", "мікрорайон",
)


# --------------------------------------------------------------------------- #
# FSM states
# --------------------------------------------------------------------------- #
class ReportState(StatesGroup):
    step_id1 = State()
    step_id2 = State()
    step_id3 = State()
    step_id4 = State()
    step_media = State()
    step_contact = State()
    post_menu = State()


# --------------------------------------------------------------------------- #
# In-memory tracking (per-process). For multi-worker deploys move to Redis/DB.
# --------------------------------------------------------------------------- #
user_last_report: Dict[int, datetime] = {}
recent_reports: List[Tuple[str, str, str, datetime]] = []

excel_lock = asyncio.Lock()
queue_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def get_user_hash(user_id: int) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:12]


def _normalize(text: str) -> str:
    return text.strip().lower()


def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    now = datetime.now()
    last = user_last_report.get(user_id)
    if last is not None:
        elapsed = (now - last).total_seconds()
        window = RATE_LIMIT_MINUTES * 60
        if elapsed < window:
            wait_min = int((window - elapsed) // 60) + 1
            return True, wait_min
    return False, 0


def _prune_recent(now: Optional[datetime] = None) -> None:
    now = now or datetime.now()
    cutoff = now - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    recent_reports[:] = [r for r in recent_reports if r[3] > cutoff]


def check_duplicate(user_hash: str, id1: str, id2: str) -> bool:
    _prune_recent()
    id1_n, id2_n = _normalize(id1), _normalize(id2)
    return any(
        h == user_hash and i1 == id1_n and i2 == id2_n
        for (h, i1, i2, _ts) in recent_reports
    )


def _remember_report(user_hash: str, id1: str, id2: str) -> None:
    recent_reports.append((user_hash, _normalize(id1), _normalize(id2), datetime.now()))
    _prune_recent()


def get_skip_kb(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=CONFIG.get("BTN_SKIP", "Skip"),
                callback_data=callback_data,
            )]
        ]
    )


def get_post_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=CONFIG.get("BTN_ADD_ANOTHER", "Add another"),
                callback_data="add_another",
            )],
            [InlineKeyboardButton(
                text=CONFIG.get("BTN_FINISH", "Finish"),
                callback_data="finish_session",
            )],
        ]
    )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
async def queue_failed_report(data: dict) -> None:
    async with queue_lock:
        try:
            queue: List[dict] = []
            if os.path.exists(QUEUE_PATH):
                with open(QUEUE_PATH, "r", encoding="utf-8") as f:
                    queue = json.load(f)
            queue.append(data)
            with open(QUEUE_PATH, "w", encoding="utf-8") as f:
                json.dump(queue, f, ensure_ascii=False, indent=2)
            logger.warning("Report queued for retry (queue size=%d).", len(queue))
        except Exception as exc:
            logger.error("Failed to write retry queue: %s", exc)


def _append_row_sync(data: dict) -> None:
    """Blocking Excel append (formatting + media hyperlinks live in
    utils/excel_report.py). Runs inside a thread to avoid blocking the loop."""
    excel_report.append_row(EXCEL_PATH, data)


async def save_to_excel(data: dict) -> bool:
    async with excel_lock:
        try:
            await asyncio.to_thread(_append_row_sync, data)
            logger.info("Row appended (hash=%s).", data.get("user_hash", "?"))
            return True
        except Exception as exc:
            logger.error("Excel export failed: %s", exc)
            await queue_failed_report(data)
            return False


async def _store_media(message: Message) -> str:
    if message.photo:
        file_id = message.photo[-1].file_id
        ext = ".jpg"
    elif message.video:
        file_id = message.video.file_id
        ext = ".mp4"
    elif message.document:
        file_id = message.document.file_id
        ext = os.path.splitext(message.document.file_name or "")[1] or ".bin"
    else:
        return ""

    if not SAVE_MEDIA_FILES:
        return file_id

    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = MEDIA_DIR / f"{stamp}{ext}"
        await message.bot.download(file_id, destination=dest)
        return str(dest)
    except Exception as exc:
        logger.error("Media download failed, storing file_id instead: %s", exc)
        return file_id


# --------------------------------------------------------------------------- #
# FSM handlers
# --------------------------------------------------------------------------- #
async def begin_report(user_id: int, target: Message, state: FSMContext) -> None:
    limited, wait_min = check_rate_limit(user_id)
    if limited:
        msg = CONFIG.get("RATE_LIMIT_MSG", "Please wait {X} minutes.")
        try:
            msg = msg.format(X=wait_min)
        except (KeyError, IndexError, ValueError):
            pass
        await target.answer(msg)
        return

    user_last_report[user_id] = datetime.now()
    await state.clear()
    await state.update_data(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        deeplink=deeplink.get_payload(user_id),
    )
    await state.set_state(ReportState.step_id1)
    await target.answer(CONFIG.get("STEP_ID1_PROMPT", "Enter ID1:"))


@router.message(Command("report"))
@router.message(F.text == CONFIG.get("MENU_REPORT_BTN", "__report__"))
async def start_report(message: Message, state: FSMContext) -> None:
    await begin_report(message.from_user.id, message, state)


@router.callback_query(F.data == "menu_report")
async def start_report_from_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await begin_report(callback.from_user.id, callback.message, state)
    await callback.answer()


@router.message(ReportState.step_id1, F.text)
async def process_id1(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < MIN_LEN_ID1:
        await message.answer(CONFIG.get("VALID_SHORT_ID1", "Please enter a valid identifier."))
        return
    await state.update_data(id1=text)
    await state.set_state(ReportState.step_id2)
    await message.answer(CONFIG.get("STEP_ID2_PROMPT", "Enter address:"))


@router.message(ReportState.step_id2, F.text)
async def process_id2(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    lowered = text.lower()

    if len(text) < MIN_LEN_ID2 or not any(m in lowered for m in ID2_MARKERS):
        await message.answer(CONFIG.get("VALID_SHORT_ID2", "Please provide a valid address."))
        return

    data = await state.get_data()
    user_hash = get_user_hash(message.from_user.id)

    if check_duplicate(user_hash, data.get("id1", ""), text):
        await message.answer(CONFIG.get("DUPLICATE_MSG", "This entry already exists today."))
        return

    await state.update_data(id2=text, user_hash=user_hash)
    await state.set_state(ReportState.step_id3)
    await message.answer(CONFIG.get("STEP_ID3_PROMPT", "Describe the schedule:"))


@router.message(ReportState.step_id3, F.text)
async def process_id3(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < MIN_LEN_ID3:
        await message.answer(CONFIG.get("VALID_SHORT_ID3", "Please add more detail about the schedule."))
        return
    await state.update_data(id3=text)
    await state.set_state(ReportState.step_id4)
    await message.answer(CONFIG.get("STEP_ID4_PROMPT", "Specify regular locations:"))


@router.message(ReportState.step_id4, F.text)
async def process_id4(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < MIN_LEN_ID4:
        await message.answer(CONFIG.get("VALID_SHORT_ID4", "Please specify the location."))
        return
    await state.update_data(id4=text)
    await state.set_state(ReportState.step_media)
    await message.answer(
        CONFIG.get("STEP_MEDIA_PROMPT", "Attach media (optional)."),
        reply_markup=get_skip_kb("skip_media"),
    )


@router.message(ReportState.step_media, F.photo | F.video | F.document)
async def process_media_file(message: Message, state: FSMContext) -> None:
    media_ref = await _store_media(message)
    await state.update_data(media=media_ref)
    await state.set_state(ReportState.step_contact)
    await message.answer(
        CONFIG.get("STEP_CONTACT_PROMPT", "Leave a contact (optional)."),
        reply_markup=get_skip_kb("skip_contact"),
    )


@router.message(ReportState.step_media, F.text)
async def media_wrong_type(message: Message, state: FSMContext) -> None:
    await message.answer(
        CONFIG.get("STEP_MEDIA_PROMPT", "Attach media (optional)."),
        reply_markup=get_skip_kb("skip_media"),
    )


@router.callback_query(F.data == "skip_media", ReportState.step_media)
async def skip_media(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(media="")
    await state.set_state(ReportState.step_contact)
    await callback.message.answer(
        CONFIG.get("STEP_CONTACT_PROMPT", "Leave a contact (optional)."),
        reply_markup=get_skip_kb("skip_contact"),
    )
    await callback.answer()


@router.message(ReportState.step_contact, F.text)
async def process_contact(message: Message, state: FSMContext) -> None:
    await state.update_data(contact=(message.text or "").strip())
    await finalize_report(message, state)


@router.callback_query(F.data == "skip_contact", ReportState.step_contact)
async def skip_contact(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(contact="")
    await finalize_report(callback.message, state)
    await callback.answer()


async def finalize_report(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    saved = await save_to_excel(data)

    # Notify the admin about the new/changed record (opt-in, never blocks flow).
    await notifier.notify(message.bot, data, saved=saved)

    if saved and sharepoint.is_configured():
        await sharepoint.upload_report(EXCEL_PATH)

    user_hash = data.get("user_hash", "")
    if user_hash:
        _remember_report(user_hash, data.get("id1", ""), data.get("id2", ""))

    await state.set_state(ReportState.post_menu)
    await message.answer(
        CONFIG.get("SUCCESS_MSG", "Thank you for your submission!"),
        reply_markup=get_post_menu_kb(),
    )


# --------------------------------------------------------------------------- #
# Post-session menu
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "add_another")
async def add_another(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        deeplink=deeplink.get_payload(callback.from_user.id),
    )
    await state.set_state(ReportState.step_id1)
    await callback.message.answer(CONFIG.get("STEP_ID1_PROMPT", "Enter ID1:"))
    await callback.answer()


@router.callback_query(F.data == "finish_session")
async def finish_session(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(CONFIG.get("FINISH_MSG", "Session finished. Thank you!"))
    await callback.answer()
    