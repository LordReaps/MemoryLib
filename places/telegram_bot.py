import logging
from io import BytesIO
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

from places.models import ChangeRequest, Photo
from places.services import approve_change_request, build_change_request_message, reject_change_request


logger = logging.getLogger(__name__)


def _bot_token():
    return getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""


def _admin_chat_id():
    value = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "") or ""
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pending_requests():
    return list(
        ChangeRequest.objects.filter(status=ChangeRequest.STATUS_PENDING).order_by("created_at")
    )


def _save_telegram_message(request_id, admin_chat_id, message_id, photos_sent):
    change_request = ChangeRequest.objects.get(pk=request_id)
    change_request.telegram_chat_id = str(admin_chat_id)
    change_request.telegram_message_id = str(message_id)
    change_request.telegram_photos_sent = photos_sent
    change_request.save(
        update_fields=["telegram_chat_id", "telegram_message_id", "telegram_photos_sent"]
    )


def _mark_telegram_photos_sent(request_id):
    ChangeRequest.objects.filter(pk=request_id).update(telegram_photos_sent=True)


def _request_photos(change_request):
    photos = []
    if change_request.action == ChangeRequest.ACTION_UPDATE:
        for photo in Photo.objects.filter(place_id=change_request.entity_id).order_by("sort_order", "id"):
            if photo.image:
                with photo.image.open("rb") as image_file:
                    photos.append((photo.original_filename or Path(photo.image.name).name, image_file.read()))

    payload_files = (change_request.payload or {}).get("files") or []
    if payload_files:
        request_dir = Path(
            change_request.temp_storage_path
            or (Path(settings.MEDIA_ROOT) / "change_requests" / str(change_request.pk))
        )
        files_dir = request_dir / "files"
        for item in payload_files:
            stored_name = item.get("stored_name")
            if not stored_name:
                continue
            file_path = files_dir / stored_name
            with file_path.open("rb") as image_file:
                photos.append((item.get("original_name") or file_path.name, image_file.read()))

    return photos


def _prepare_telegram_photo(filename, content):
    max_size_bytes = 9_500_000
    with Image.open(BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((10_000, 10_000))

        for _ in range(8):
            for quality in (85, 75, 65, 55, 45):
                output = BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                if output.tell() <= max_size_bytes:
                    return f"{Path(filename).stem}.jpg", output.getvalue()
            image.thumbnail((int(image.width * 0.8), int(image.height * 0.8)))

    raise ValueError(f"Не удалось уменьшить изображение для Telegram: {filename}")


async def _send_request_photos(bot, chat_id, photos, reply_to_message_id=None):
    prepared_photos = [
        _prepare_telegram_photo(filename, content)
        for filename, content in photos
    ]
    for index in range(0, len(prepared_photos), 10):
        photo_batch = prepared_photos[index : index + 10]
        if len(photo_batch) == 1:
            filename, content = photo_batch[0]
            await bot.send_photo(
                chat_id=chat_id,
                photo=BytesIO(content),
                filename=filename,
                reply_to_message_id=reply_to_message_id,
            )
            continue

        media = [
            InputMediaPhoto(media=BytesIO(content), filename=filename)
            for filename, content in photo_batch
        ]
        await bot.send_media_group(
            chat_id=chat_id,
            media=media,
            reply_to_message_id=reply_to_message_id,
        )


def _keyboard_for_request(change_request):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve:{change_request.pk}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{change_request.pk}"),
            ]
        ]
    )


async def send_pending_requests(context: ContextTypes.DEFAULT_TYPE):
    token = _bot_token()
    if not token:
        raise ImproperlyConfigured("TELEGRAM_BOT_TOKEN не задан.")

    admin_chat_id = _admin_chat_id()
    if admin_chat_id is None:
        raise ImproperlyConfigured("TELEGRAM_ADMIN_CHAT_ID не задан или имеет неверный формат.")

    requests = await sync_to_async(_pending_requests)()
    logger.info("Telegram queue poll: pending_requests=%d", len(requests))
    for request in requests:
        try:
            already_notified = (
                request.telegram_message_id
                and request.telegram_chat_id == str(admin_chat_id)
            )
            if already_notified and request.telegram_photos_sent:
                continue

            photos = await sync_to_async(_request_photos)(request)
            if already_notified:
                await _send_request_photos(
                    context.bot,
                    admin_chat_id,
                    photos,
                    reply_to_message_id=int(request.telegram_message_id),
                )
                await sync_to_async(_mark_telegram_photos_sent)(request.pk)
                logger.info(
                    "Telegram photos sent for existing message: change_request_id=%s, photo_count=%d",
                    request.pk,
                    len(photos),
                )
                continue

            await _send_request_photos(context.bot, admin_chat_id, photos)
            text = build_change_request_message(request, file_count=len(photos))
            text = text[:4093] + "..." if len(text) > 4096 else text

            message = await context.bot.send_message(
                chat_id=admin_chat_id,
                text=text,
                disable_web_page_preview=True,
                reply_markup=_keyboard_for_request(request),
            )

            await sync_to_async(_save_telegram_message)(
                request.pk,
                admin_chat_id,
                message.message_id,
                True,
            )
        except Exception:
            logger.exception("Не удалось отправить заявку #%s в Telegram", request.pk)
            raise
        logger.info(
            "Telegram message sent: change_request_id=%s, message_id=%s",
            request.pk,
            message.message_id,
        )
        if photos:
            logger.info(
                "Telegram photos sent: change_request_id=%s, photo_count=%d",
                request.pk,
                len(photos),
            )


async def handle_telegram_error(update, context):
    logger.error("Telegram bot error while handling update: %s", context.error, exc_info=context.error)


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
            await sync_to_async(approve_change_request)(request_id, reviewed_by="telegram")
            await query.answer("Заявка подтверждена", show_alert=True)
        except Exception as exc:
            logger.exception("Не удалось подтвердить заявку #%s в Telegram", request_id)
            await query.answer(f"Ошибка: {exc}", show_alert=True)
        return

    if prefix == "reject":
        try:
            await sync_to_async(reject_change_request)(request_id, reviewed_by="telegram")
            await query.answer("Заявка отклонена", show_alert=True)
        except Exception as exc:
            logger.exception("Не удалось отклонить заявку #%s в Telegram", request_id)
            await query.answer(f"Ошибка: {exc}", show_alert=True)
        return

    await query.answer("Неизвестная команда", show_alert=False)


async def start_command(update, context):
    await update.message.reply_text("Бот готов к подтверждению заявок.")


def start_telegram_bot():
    token = _bot_token()
    has_admin_chat = _admin_chat_id() is not None
    logger.info(
        "Starting Telegram approval bot: token_configured=%s, admin_chat_configured=%s",
        bool(token),
        has_admin_chat,
    )
    if not token:
        raise ImproperlyConfigured("TELEGRAM_BOT_TOKEN не задан.")
    if not has_admin_chat:
        raise ImproperlyConfigured("TELEGRAM_ADMIN_CHAT_ID не задан или имеет неверный формат.")

    application = ApplicationBuilder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(handle_telegram_error)
    application.job_queue.run_repeating(send_pending_requests, interval=10, first=1)
    logger.info("Telegram bot polling started; checking queue every 3 seconds")
    application.run_polling()