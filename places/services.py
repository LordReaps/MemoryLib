import json
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .models import ChangeRequest, Photo, Place


class ServiceOperationError(RuntimeError):
    """Бизнес-ошибка сервиса: операция с местом или фотографиями не выполнена."""


def serialize_place(place):
    return {
        "id": place.id,
        "title": place.title,
        "description": place.description,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "start_date": place.start_date.isoformat() if place.start_date else None,
        "end_date": place.end_date.isoformat() if place.end_date else None,
        "photos": [
            {
                "id": p.id,
                "thumb_url": reverse("places:serve_photo", kwargs={"pk": p.id, "file_type": "thumb"}) if p.thumbnail else None,
                "original_url": reverse("places:serve_photo", kwargs={"pk": p.id, "file_type": "original"}),
            }
            for p in place.photos.all()
        ],
    }


def _build_place(**fields):
    place = Place(
        title=fields["title"],
        description=fields.get("description") or "",
        latitude=fields["latitude"],
        longitude=fields["longitude"],
        start_date=fields.get("start_date"),
        end_date=fields.get("end_date"),
    )
    place.full_clean()
    return place


@transaction.atomic
def create_place(**fields):
    place = _build_place(**fields)
    place.save()
    files = fields.pop("files", [])
    if files:
        add_photos_to_place(place.id, files)
    return place


@transaction.atomic
def update_place(place, **fields):
    files = fields.pop("files", [])
    place.title = fields["title"]
    place.description = fields.get("description") or ""
    place.latitude = fields["latitude"]
    place.longitude = fields["longitude"]
    place.start_date = fields.get("start_date")
    place.end_date = fields.get("end_date")
    place.full_clean()
    place.save()
    if files:
        add_photos_to_place(place.id, files)
    return place


@transaction.atomic
def delete_place(place):
    delete_photos_for_place(place.id)
    place.delete()


def _process_image(uploaded_file, place_id, sort_order):
    try:
        img = Image.open(uploaded_file)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        thumb = img.copy()
        thumb.thumbnail((400, 400))

        thumb_io = BytesIO()
        thumb.save(thumb_io, format="WEBP", quality=85)
        thumb_file = ContentFile(thumb_io.getvalue())

        photo = Photo(
            place_id=place_id,
            original_filename=getattr(uploaded_file, "name", "photo.jpg"),
            mime_type=getattr(uploaded_file, "content_type", "image/jpeg") or "image/jpeg",
            size_bytes=getattr(uploaded_file, "size", 0) or 0,
            sort_order=sort_order,
        )

        img_io = BytesIO()
        img.save(img_io, format="JPEG", quality=90)
        safe_name = f"{uuid.uuid4().hex}.jpg"
        photo.image.save(safe_name, ContentFile(img_io.getvalue()), save=False)

        safe_thumb_name = f"{uuid.uuid4().hex}.webp"
        photo.thumbnail.save(safe_thumb_name, thumb_file, save=False)

        photo.save()
        return photo
    except Exception as exc:
        raise ServiceOperationError(f"Не удалось обработать фото '{getattr(uploaded_file, 'name', 'unknown')}': {exc}") from exc


def add_photos_to_place(place_id, files):
    photos = []
    from django.db.models import Max

    max_order = Photo.objects.filter(place_id=place_id).aggregate(Max("sort_order"))["sort_order__max"] or 0
    for index, uploaded_file in enumerate(files):
        photo = _process_image(uploaded_file, place_id, max_order + index + 1)
        photos.append(photo)
    return photos


def delete_photo_files(photo):
    if photo.image:
        photo.image.delete(save=False)
    if photo.thumbnail:
        photo.thumbnail.delete(save=False)


def delete_photo(photo):
    delete_photo_files(photo)
    photo.delete()


def delete_photos_for_place(place_id):
    photos = list(Photo.objects.filter(place_id=place_id))
    for photo in photos:
        delete_photo_files(photo)
    Photo.objects.filter(place_id=place_id).delete()


# --- stage 8 change request flow ---

def _change_requests_root():
    return Path(settings.MEDIA_ROOT) / "change_requests"


def _get_temp_directory(change_request):
    if change_request.temp_storage_path:
        return Path(change_request.temp_storage_path)
    root = _change_requests_root() / str(change_request.pk)
    change_request.temp_storage_path = str(root)
    change_request.save(update_fields=["temp_storage_path"])
    return root


def _cleanup_temp_directory(change_request):
    path = Path(change_request.temp_storage_path) if change_request.temp_storage_path else None
    if path and path.exists():
        shutil.rmtree(path, ignore_errors=True)
    change_request.temp_storage_path = ""
    change_request.save(update_fields=["temp_storage_path"])


def _save_queued_files(change_request, uploaded_files):
    if not uploaded_files:
        return []
    temp_dir = _get_temp_directory(change_request)
    files_dir = temp_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for index, uploaded in enumerate(uploaded_files):
        content = uploaded.read()
        original_name = getattr(uploaded, "name", f"photo_{index}.jpg") or f"photo_{index}.jpg"
        safe_name = f"{uuid.uuid4().hex}_{Path(original_name).name}"
        destination = files_dir / safe_name
        destination.write_bytes(content)
        saved.append({
            "stored_name": safe_name,
            "original_name": original_name,
            "mime_type": getattr(uploaded, "content_type", "application/octet-stream") or "application/octet-stream",
            "size_bytes": len(content),
        })
        if hasattr(uploaded, "seek"):
            try:
                uploaded.seek(0)
            except Exception:
                pass
    change_request.payload["files"] = saved
    change_request.save(update_fields=["payload"])
    return saved


def _queued_files_from_payload(change_request):
    payload = change_request.payload or {}
    items = payload.get("files") or []
    if not items:
        return []
    temp_dir = _get_temp_directory(change_request)
    files_dir = temp_dir / "files"
    result = []
    for item in items:
        stored_name = item.get("stored_name")
        if not stored_name:
            continue
        path = files_dir / stored_name
        if not path.exists():
            continue
        content = path.read_bytes()
        result.append(ContentFile(content, name=item.get("original_name") or stored_name))
    return result


def _request_hash_for_payload(payload):
    return uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(payload, sort_keys=True, default=str)).hex


def queue_place_create(**fields):
    uploaded_files = fields.pop("files", [])
    payload = {
        "title": fields["title"],
        "description": fields.get("description") or "",
        "latitude": fields["latitude"],
        "longitude": fields["longitude"],
        "start_date": fields.get("start_date"),
        "end_date": fields.get("end_date"),
    }
    change_request = ChangeRequest.objects.create(
        action=ChangeRequest.ACTION_CREATE,
        entity_type=ChangeRequest.ENTITY_PLACE,
        payload=payload,
        status=ChangeRequest.STATUS_PENDING,
    )
    change_request.request_hash = _request_hash_for_payload(payload)
    change_request.save(update_fields=["request_hash"])
    _save_queued_files(change_request, uploaded_files)
    return change_request


def queue_place_update(place, **fields):
    uploaded_files = fields.pop("files", [])
    payload = {
        "place_id": place.pk,
        "title": fields["title"],
        "description": fields.get("description") or "",
        "latitude": fields["latitude"],
        "longitude": fields["longitude"],
        "start_date": fields.get("start_date"),
        "end_date": fields.get("end_date"),
    }
    change_request = ChangeRequest.objects.create(
        action=ChangeRequest.ACTION_UPDATE,
        entity_type=ChangeRequest.ENTITY_PLACE,
        entity_id=place.pk,
        payload=payload,
        status=ChangeRequest.STATUS_PENDING,
    )
    change_request.request_hash = _request_hash_for_payload(payload)
    change_request.save(update_fields=["request_hash"])
    _save_queued_files(change_request, uploaded_files)
    return change_request


def queue_place_delete(place):
    payload = {
        "place_id": place.pk,
        "title": place.title,
        "description": place.description,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "start_date": str(place.start_date) if place.start_date else None,
        "end_date": str(place.end_date) if place.end_date else None,
    }
    change_request = ChangeRequest.objects.create(
        action=ChangeRequest.ACTION_DELETE,
        entity_type=ChangeRequest.ENTITY_PLACE,
        entity_id=place.pk,
        payload=payload,
        status=ChangeRequest.STATUS_PENDING,
    )
    change_request.request_hash = _request_hash_for_payload(payload)
    change_request.save(update_fields=["request_hash"])
    return change_request


def reject_change_request(change_request_id, reviewed_by="system"):
    change_request = ChangeRequest.objects.get(pk=change_request_id)
    change_request.status = ChangeRequest.STATUS_REJECTED
    change_request.reviewed_at = timezone.now()
    change_request.reviewed_by = reviewed_by
    change_request.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
    _cleanup_temp_directory(change_request)
    return change_request


@transaction.atomic
def apply_change_request(change_request_id, reviewed_by="system"):
    change_request = ChangeRequest.objects.get(pk=change_request_id)
    if change_request.status not in {ChangeRequest.STATUS_PENDING, ChangeRequest.STATUS_APPROVED}:
        raise ServiceOperationError(f"Заявка #{change_request.pk} не ожидает применения.")

    try:
        payload = change_request.payload or {}
        queued_files = _queued_files_from_payload(change_request)

        if change_request.action == ChangeRequest.ACTION_CREATE:
            place = create_place(
                title=payload.get("title"),
                description=payload.get("description") or "",
                latitude=payload.get("latitude"),
                longitude=payload.get("longitude"),
                start_date=payload.get("start_date") or None,
                end_date=payload.get("end_date") or None,
                files=queued_files,
            )
            change_request.entity_id = place.pk

        elif change_request.action == ChangeRequest.ACTION_UPDATE:
            place = Place.objects.get(pk=change_request.entity_id)
            update_place(
                place,
                title=payload.get("title"),
                description=payload.get("description") or "",
                latitude=payload.get("latitude"),
                longitude=payload.get("longitude"),
                start_date=payload.get("start_date") or None,
                end_date=payload.get("end_date") or None,
                files=queued_files,
            )

        elif change_request.action == ChangeRequest.ACTION_DELETE:
            place = Place.objects.get(pk=change_request.entity_id)
            delete_place(place)

        else:
            raise ServiceOperationError(f"Неизвестный тип заявки: {change_request.action}")

        change_request.status = ChangeRequest.STATUS_APPLIED
        change_request.reviewed_at = timezone.now()
        change_request.reviewed_by = reviewed_by
        change_request.error_message = ""
        change_request.save(update_fields=["status", "entity_id", "reviewed_at", "reviewed_by", "error_message", "updated_at"])
        _cleanup_temp_directory(change_request)
        return change_request

    except Exception as exc:
        change_request.status = ChangeRequest.STATUS_FAILED
        change_request.reviewed_at = timezone.now()
        change_request.reviewed_by = reviewed_by
        change_request.error_message = str(exc)
        change_request.save(update_fields=["status", "reviewed_at", "reviewed_by", "error_message", "updated_at"])
        _cleanup_temp_directory(change_request)
        raise ServiceOperationError(f"Не удалось применить заявку #{change_request.pk}: {exc}") from exc


def approve_change_request(change_request_id, reviewed_by="system"):
    change_request = ChangeRequest.objects.get(pk=change_request_id)
    change_request.status = ChangeRequest.STATUS_APPROVED
    change_request.reviewed_at = timezone.now()
    change_request.reviewed_by = reviewed_by
    change_request.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
    return apply_change_request(change_request.pk, reviewed_by=reviewed_by)


def build_change_request_message(change_request):
    payload = change_request.payload or {}
    lines = [
        f"Заявка #{change_request.pk}",
        f"Действие: {change_request.get_action_display()}",
    ]
    if change_request.action == ChangeRequest.ACTION_CREATE:
        lines.append(f"Название: {payload.get('title', '—')}")
        lines.append(f"Координаты: {payload.get('latitude', '—')}, {payload.get('longitude', '—')}")
        lines.append(f"Фотографии: {len(payload.get('files') or [])}")
    elif change_request.action == ChangeRequest.ACTION_UPDATE:
        lines.append(f"Место: #{change_request.entity_id}")
        lines.append(f"Название: {payload.get('title', '—')}")
    elif change_request.action == ChangeRequest.ACTION_DELETE:
        lines.append(f"Удаление места: #{change_request.entity_id}")
    lines.append("Подтвердить / Отклонить")
    return "\n".join(lines)


def build_telegram_callback_payload(prefix, change_request_id):
    return f"{prefix}:{change_request_id}"


# keep old serializations available
from django.urls import reverse
from io import BytesIO
from PIL import Image, ImageOps
from django.core.files.base import ContentFile


__all__ = [
    "ServiceOperationError",
    "serialize_place",
    "create_place",
    "update_place",
    "delete_place",
    "add_photos_to_place",
    "delete_photo_files",
    "delete_photo",
    "delete_photos_for_place",
    "queue_place_create",
    "queue_place_update",
    "queue_place_delete",
    "approve_change_request",
    "reject_change_request",
    "apply_change_request",
    "build_change_request_message",
    "build_telegram_callback_payload",
]

