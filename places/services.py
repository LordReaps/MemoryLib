import json
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from .models import ChangeRequest, Photo, Place


class ServiceOperationError(RuntimeError):
    """Бизнес-ошибка сервиса: операция с местом или фотографиями не выполнена."""


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
    if not path:
        return
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    change_request.temp_storage_path = ""
    change_request.save(update_fields=["temp_storage_path"])


def _save_queued_files(change_request, uploaded_files):
    if not uploaded_files:
        return []
    temp_dir = _get_temp_directory(change_request)
    files_dir = temp_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for index, uploaded in enumerate(uploaded_files):
        content = uploaded.read()
        original_name = getattr(uploaded, "name", f"photo_{index}.jpg") or f"photo_{index}.jpg"
        safe_name = f"{uuid.uuid4().hex}_{Path(original_name).name}"
        destination = files_dir / safe_name
        destination.write_bytes(content)
        saved_files.append(
            {
                "stored_name": safe_name,
                "original_name": original_name,
                "mime_type": getattr(uploaded, "content_type", "application/octet-stream") or "application/octet-stream",
                "size_bytes": len(content),
            }
        )
        if hasattr(uploaded, "seek"):
            try:
                uploaded.seek(0)
            except Exception:
                pass

    change_request.payload["files"] = saved_files
    change_request.save(update_fields=["payload"])
    return saved_files


def _queued_files_from_payload(change_request):
    files_payload = (change_request.payload or {}).get("files") or []
    if not files_payload:
        return []

    temp_dir = _get_temp_directory(change_request)
    files_dir = temp_dir / "files"
    if not files_dir.exists():
        return []

    result = []
    for item in files_payload:
        stored_name = item.get("stored_name")
        if not stored_name:
            continue
        path = files_dir / stored_name
        if not path.exists():
            continue
        data = path.read_bytes()
        result.append(
            SimpleUploadedFile(
                name=item.get("original_name") or stored_name,
                content=data,
                size=len(data),
            )
        )
    return result


def _queued_object_from_payload(change_request):
    payload = change_request.payload or {}
    return {
        "title": payload.get("title"),
        "description": payload.get("description") or "",
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "start_date": payload.get("start_date") or None,
        "end_date": payload.get("end_date") or None,
    }


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
        raise ServiceOperationError(f"Заявка #{change_request.pk} не находится в статусе ожидания.")

    try:
        queued_files = _queued_files_from_payload(change_request)
        payload = change_request.payload or {}

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
    lines = [
        f"Заявка #{change_request.pk}",
        f"Действие: {change_request.get_action_display()}",
        f"Сущность: {change_request.get_entity_type_display()}",
    ]

    payload = change_request.payload or {}
    if change_request.action == ChangeRequest.ACTION_CREATE:
        lines.append(f"Название: {payload.get('title', '—')}")
        lines.append(f"Координаты: {payload.get('latitude', '—')}, {payload.get('longitude', '—')}")
        files = payload.get("files") or []
        lines.append(f"Фотографии: {len(files)}")
    elif change_request.action == ChangeRequest.ACTION_UPDATE:
        lines.append(f"Место: #{change_request.entity_id}")
        lines.append(f"Название: {payload.get('title', '—')}")
    elif change_request.action == ChangeRequest.ACTION_DELETE:
        lines.append(f"Место: #{change_request.entity_id}")

    lines.append("Подтвердить / Отклонить")
    return "\n".join(lines)


def build_telegram_callback_payload(prefix, change_request_id):
    return f"{prefix}:{change_request_id}"


# Legacy aliases kept for compatibility with existing code.
# The queue/apply/reject flow is now centralized here.

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
    "delete_photo_by_id",
    "ChangeRequest",
    "queue_place_create",
    "queue_place_update",
    "queue_place_delete",
    "approve_change_request",
    "reject_change_request",
    "apply_change_request",
    "build_change_request_message",
    "build_telegram_callback_payload",
]
