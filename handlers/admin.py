"""
Small admin utilities kept separate from the feedback flow.

/myid — replies with the chat/user id so the admin can copy it into
NOTIFY_CHAT_ID (required for reliable admin notifications).
"""

import logging
import secrets

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from utils import deeplink

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(
        "🆔 <b>Ваші ідентифікатори</b>\n"
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"user_id: <code>{message.from_user.id}</code>\n\n"
        "Вкажіть <code>chat_id</code> у змінній <code>NOTIFY_CHAT_ID</code>, "
        "щоб отримувати сповіщення про нові записи."
    )


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject) -> None:
    """Generate a shareable deep link. Optional argument = custom payload,
    otherwise a random token is used. The payload is recorded in every report
    made by a user who opened the link."""
    payload = (command.args or "").strip() or secrets.token_urlsafe(6).replace("-", "_")
    if not deeplink.is_valid(payload):
        await message.answer(
            "⚠️ Некоректний payload. Дозволені лише A–Z, a–z, 0–9, _ та - (до 64 символів)."
        )
        return
    me = await message.bot.get_me()
    url = deeplink.build_link(me.username, payload)
    await message.answer(
        "🔗 <b>Deep-link</b>\n"
        f"Мітка: <code>{payload}</code>\n"
        f"{url}\n\n"
        "Усі відгуки від користувачів, що перейшли за цим посиланням, "
        "фіксуватимуть цю мітку у звіті."
    )
