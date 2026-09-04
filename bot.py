import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()


def _require_config() -> tuple[str, int]:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not ADMIN_CHAT_ID:
        raise RuntimeError("ADMIN_CHAT_ID is not set")
    return BOT_TOKEN, int(ADMIN_CHAT_ID)


def _user_label(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Unknown user"
    username = f"@{user.username}" if user.username else "no username"
    name = " ".join(part for part in [user.first_name, user.last_name] if part)
    return f"{name} ({username}, id={user.id})".strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Напиши сюда сообщение, и оно придет админу.",
    )


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    admin_chat_id = context.application.bot_data["admin_chat_id"]
    user = update.effective_user
    label = _user_label(update)

    header = f"<b>Новое сообщение от пользователя</b>\n<b>От:</b> {label}"

    if update.message.text:
        text = update.message.text
        if text.startswith("/"):
            return
        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=f"{header}\n\n{text}",
            parse_mode=ParseMode.HTML,
        )
    elif update.message.caption and (
        update.message.photo
        or update.message.document
        or update.message.video
        or update.message.audio
        or update.message.voice
        or update.message.sticker
    ):
        caption = update.message.caption
        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=f"{header}\n\n<b>Подпись:</b>\n{caption}",
            parse_mode=ParseMode.HTML,
        )
        await _forward_content(update, context, admin_chat_id)
    else:
        await _forward_content(update, context, admin_chat_id)
        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=header,
            parse_mode=ParseMode.HTML,
        )

    await update.message.reply_text("Спасибо! Сообщение отправлено.")


async def _forward_content(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_chat_id: int) -> None:
    if not update.message:
        return
    await context.bot.copy_message(
        chat_id=admin_chat_id,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


def main() -> None:
    token, admin_chat_id = _require_config()
    # Python 3.14 no longer guarantees a current loop in the main thread.
    # python-telegram-bot expects one to exist before run_polling() starts.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(token).build()
    app.bot_data["admin_chat_id"] = admin_chat_id

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_feedback))
    app.add_error_handler(error_handler)

    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
