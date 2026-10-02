import asyncio
import html
import logging
import os

import httpx
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
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

# Skillo API used to confirm new registrations.
SKILLO_API_URL = os.getenv("SKILLO_API_URL", "").strip().rstrip("/")
SKILLO_BOT_SECRET = os.getenv("SKILLO_BOT_SECRET", "").strip()

# Deep-link tokens are random base64url strings; anything longer is bogus.
MAX_TOKEN_LENGTH = 200


def _user_label(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Unknown user"
    username = f"@{user.username}" if user.username else "no username"
    name = " ".join(part for part in [user.first_name, user.last_name] if part)
    return f"{name} ({username}, id={user.id})".strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles both a plain /start and the registration deep link /start <token>."""
    if not update.message:
        return

    token = context.args[0].strip() if context.args else ""

    if not token:
        await update.message.reply_text(
            "Привет! Это бот поддержки Skillo.\n\n"
            "Напиши сюда сообщение — оно придёт администратору.\n"
            "Аккаунт подтверждается на сайте: нажми «Подтвердить через Telegram», "
            "и я пришлю код."
        )
        return

    if len(token) > MAX_TOKEN_LENGTH:
        await update.message.reply_text(
            "Ссылка повреждена. Вернитесь на сайт Skillo и запросите подтверждение заново."
        )
        return

    if not SKILLO_API_URL or not SKILLO_BOT_SECRET:
        logger.error(
            "Registration deep link received, but SKILLO_API_URL / SKILLO_BOT_SECRET are not set"
        )
        await update.message.reply_text(
            "Подтверждение аккаунтов пока не настроено. Напишите администратору."
        )
        return

    chat_id = update.effective_chat.id
    username = update.effective_user.username if update.effective_user else None

    try:
        data = await _confirm_registration(token, chat_id, username)
    except httpx.HTTPStatusError as error:
        detail = _error_detail(error)
        logger.warning(
            "Registration confirm rejected (%s): %s",
            error.response.status_code,
            detail,
        )
        await update.message.reply_text(f"Не удалось подтвердить аккаунт: {detail}")
        return
    except Exception:
        logger.exception("Registration confirm failed for chat %s", chat_id)
        await update.message.reply_text(
            "Сервис подтверждения недоступен. Попробуйте ещё раз через минуту."
        )
        return

    code = html.escape(str(data.get("code", "")))
    email = html.escape(str(data.get("email", "")))

    await update.message.reply_text(
        f"Код подтверждения для <b>{email}</b>:\n\n"
        f"<code>{code}</code>\n\n"
        "Введите его на сайте Skillo. Код действует 10 минут.",
        parse_mode=ParseMode.HTML,
    )


async def _confirm_registration(token: str, chat_id: int, username: str | None) -> dict:
    payload = {"token": token, "chatId": str(chat_id), "username": username}
    headers = {"X-Bot-Secret": SKILLO_BOT_SECRET}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{SKILLO_API_URL}/api/auth/telegram/confirm",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


def _error_detail(error: httpx.HTTPStatusError) -> str:
    try:
        body = error.response.json()
    except Exception:
        return "ссылка недействительна или устарела"

    return str(body.get("error") or "ссылка недействительна или устарела")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Напишите сообщение — оно уйдёт администратору Skillo.\n"
        "Для подтверждения аккаунта используйте кнопку на сайте."
    )


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    admin_chat_id = context.application.bot_data["admin_chat_id"]
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
        update.message.photo or update.message.document
        or update.message.video or update.message.audio
        or update.message.voice or update.message.sticker
    ):
        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=f"{header}\n\n<b>Подпись:</b>\n{update.message.caption}",
            parse_mode=ParseMode.HTML,
        )
        await _forward_content(update, context, admin_chat_id)
    else:
        await _forward_content(update, context, admin_chat_id)
        await context.bot.send_message(chat_id=admin_chat_id, text=header, parse_mode=ParseMode.HTML)

    await update.message.reply_text("Спасибо! Сообщение отправлено.")


async def _forward_content(update, context, admin_chat_id):
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
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not ADMIN_CHAT_ID:
        raise RuntimeError("ADMIN_CHAT_ID is not set")
    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL is not set")

    if not SKILLO_API_URL or not SKILLO_BOT_SECRET:
        logger.warning(
            "SKILLO_API_URL / SKILLO_BOT_SECRET are not set — feedback works, "
            "but registration confirmation is disabled"
        )

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["admin_chat_id"] = int(ADMIN_CHAT_ID)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_feedback))
    app.add_error_handler(error_handler)

    logger.info("Bot starting in webhook mode on port %s", PORT)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=f"{WEBHOOK_URL}/webhook",
        # Telegram echoes this value back in the X-Telegram-Bot-Api-Secret-Token
        # header, which makes forged webhook calls useless.
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
