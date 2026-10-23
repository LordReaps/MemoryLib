import logging

from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

from .models import ChangeRequest
from .services import (
    approve_change_request,
    build_change_request_message,
    reject_change_request,
)

logger = logging.getLogger(__name__)


def _bot_token():
    return (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()


def _admin_chat_id():
    value = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "") or ""
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _telegram_ready():
    return bool(_bot_token()) and _admin_chat_id() is not None


async def send_pending_requests(context: ContextTypes.DEFAULT_TYPE):
    if not _telegram_ready():
        return

    admin_chat_id = _admin_chat_id()
    pending_requests = ChangeRequest.objects.filter(status=ChangeRequest.STATUS_PENDING).order_by("created_at")

    for request in pending_requests:
        if request.telegram_message_id and request.telegram_chat_id == str(admin_chat_id):
            continue

        text = build_change_request_message(request)
        message = await context.bot.send_message(
            chat_id=admin_chat_id,
            text=text,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve:{request.pk}"),
                        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{request.pk}"),
                    ]
                ]
            ),
        )

        request.telegram_chat_id = str(admin_chat_id)
        request.telegram_message_id = str(message.message_id)
        request.save(update_fields=["telegram_chat_id", "telegram_message_id"])


async def handle_callback(update, context):
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if not data:
        await query.answer("Пустой callback", show_alert=False)
        return

    try:
        prefix, request_id = data.split(":", 1)
        request_id = int(request_id)
    except ValueError:
        await query.answer("Неверный формат callback", show_alert=False)
        return

    if prefix == "approve":
        try:
            approve_change_request(request_id, reviewed_by="telegram")
            await query.answer("✅ Заявка подтверждена", show_alert=True)
        except Exception as exc:
            await query.answer(f"Ошибка: {exc}", show_alert=True)
        return

    if prefix == "reject":
        try:
            reject_change_request(request_id, reviewed_by="telegram")
            await query.answer("❌ Заявка отклонена", show_alert=True)
        except Exception as exc:
            await query.answer(f"Ошибка: {exc}", show_alert=True)
        return

    await query.answer("Неизвестная команда", show_alert=False)


async def start_command(update, context):
    await update.message.reply_text("Бот готов к подтверждению заявок.")


def start_telegram_bot():
    token = _bot_token()
    if not token:
        return

    application = ApplicationBuilder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.job_queue.run_repeating(send_pending_requests, interval=30, first=5)
    application.run_polling(drop_pending_updates=True)