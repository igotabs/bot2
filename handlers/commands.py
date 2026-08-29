"""
General commands and the /start inline menu.

All user-facing strings come from CONFIG (external file); code references only
KEY names plus a safe fallback.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from utils.config_loader import CONFIG
from utils import deeplink

router = Router()
logger = logging.getLogger(__name__)


def get_main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=CONFIG.get("MENU_REPORT_BTN", "Send feedback"),
                callback_data="menu_report",
            )],
            [InlineKeyboardButton(
                text=CONFIG.get("MENU_HELP_BTN", "How it works"),
                callback_data="menu_help",
            )],
            [InlineKeyboardButton(
                text=CONFIG.get("MENU_PRIVACY_BTN", "Privacy"),
                callback_data="menu_privacy",
            )],
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    # Capture the deep-link payload (t.me/<bot>?start=<payload>) so it can be
    # stamped onto the user's next report.
    if command.args:
        deeplink.set_payload(message.from_user.id, command.args)
    await message.answer(
        CONFIG.get("START_MSG", "Welcome!"),
        reply_markup=get_main_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(CONFIG.get("HELP_MSG", "How it works: use /report."))


@router.callback_query(F.data == "menu_help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.message.answer(CONFIG.get("HELP_MSG", "How it works: use /report."))
    await callback.answer()


@router.callback_query(F.data == "menu_privacy")
async def cb_privacy(callback: CallbackQuery) -> None:
    await callback.message.answer(CONFIG.get("PRIVACY_MSG", "Your privacy is protected."))
    await callback.answer()
