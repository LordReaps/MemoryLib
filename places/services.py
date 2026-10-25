import json
import shutil
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageOps

from .models import ChangeRequest, Photo, Place


class ServiceOperationError(RuntimeError):
    pass


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
                "thumb_url": reverse("places:serve_photo", kwargs={"pk": p.id, "file_type": "thumb"})
                if p.thumbnail
                else None,
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
    uploaded_files = fields.pop("files", [])

    place = _build_place(**fields)
    place.save()

    if uploaded_files:
        add_photos_to_place(place.id, uploaded_files)

    return place


@transaction.atomic
def update_place(place, **fields):
    uploaded_files = fields.pop("files", [])

    place.title = fields["title"]
    place.description = fields.get("description") or ""
    place.latitude = fields["latitude"]
    place.longitude = fields["longitude"]
    place.start_date = fields.get("start_date")
    place.end_date = fields.get("end_date")

    place.full_clean()
    place.save()

    if uploaded_files:
        add_photos_to_place(place.id, uploaded_files)

    return place


@transaction.atomic
def delete_place(place):
    delete_photos_for_place(place.id)
    place.delete()


def _process_image(uploaded_file, place_id, sort_order):
    photo = None
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

    except Exception:
        if photo is not None:
            try:
                delete_photo_files(photo)
            except Exception:
                pass
            try:
                photo.delete()
            except Exception:
                pass
        raise


def add_photos_to_place(place_id, files):
    created_photos = []
    try:
        max_order = Photo.objects.filter(place_id=place_id).aggregate(Max("sort_order"))["sort_order__max"] or 0

        for index, file in enumerate(files):
            photo = _process_image(file, place_id, max_order + index + 1)
            if photo:
                created_photos.append(photo)

        return created_photos

    except Exception:
        for photo in created_photos:
            try:
                delete_photo_files(photo)
            except Exception:
                pass
            try:
                photo.delete()
            except Exception:
                pass
        raise


def delete_photo_files(photo):
    if photo.image:
        try:
            photo.image.delete(save=False)
        except Exception:
            pass
    if photo.thumbnail:
        try:
            photo.thumbnail.delete(save=False)
        except Exception:
            pass


def delete_photos_for_place(place_id):
    photos = list(Photo.objects.filter(place_id=place_id))
    for photo in photos:
        delete_photo_files(photo)
    Photo.objects.filter(place_id=place_id).delete()


def delete_photo(photo):
    delete_photo_files(photo)
    photo.delete()


def _change_requests_root():
    return Path(settings.MEDIA_ROOT) / "change_requests"


def _ensure_change_request_dir(change_request):
    root = _change_requests_root()
    root.mkdir(parents=True, exist_ok=True)
    request_dir = root / str(change_request.pk)
    request_dir.mkdir(parents=True, exist_ok=True)
    return request_dir


def _cleanup_change_request_dir(change_request):
    storage_path = getattr(change_request, "temp_storage_path", "") or ""
    if not storage_path:
        return

    path = Path(storage_path)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)

    change_request.temp_storage_path = ""
    change_request.save(update_fields=["temp_storage_path"])


def _request_hash_for_payload(payload):
    return uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(payload, sort_keys=True, ensure_ascii=False)).hex


def _save_queued_files(change_request, uploaded_files):
    if not uploaded_files:
        return []

    request_dir = _ensure_change_request_dir(change_request)
    files_dir = request_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for index, uploaded_file in enumerate(uploaded_files):
        content = uploaded_file.read()
        original_name = getattr(uploaded_file, "name", f"photo_{index}.jpg")
        suffix = Path(original_name).suffix or ".jpg"
        safe_name = f"{uuid.uuid4().hex}{suffix}"
        file_path = files_dir / safe_name
        file_path.write_bytes(content)

        saved.append(
            {
                "stored_name": safe_name,
                "original_name": original_name,
                "mime_type": getattr(uploaded_file, "content_type", "application/octet-stream")
                or "application/octet-stream",
                "size_bytes": len(content),
            }
        )

    change_request.temp_storage_path = str(request_dir)
    change_request.payload["files"] = saved
    change_request.save(update_fields=["temp_storage_path", "payload"])

    return saved


def _load_queued_files(change_request):
    payload_files = change_request.payload.get("files") or []
    if not payload_files:
        return []

    request_dir = Path(change_request.temp_storage_path or (_change_requests_root() / str(change_request.pk)))
    files_dir = request_dir / "files"
    result = []

    for item in payload_files:
        stored_name = item.get("stored_name")
        if not stored_name:
            continue

        file_path = files_dir / stored_name
        if not file_path.exists():
            continue

        result.append(
            File(
                file_path.open("rb"),
                name=item.get("original_name") or file_path.name,
            )
        )

    return result


def _queue_request(action, entity_type, entity_id, payload, uploaded_files=None):
    payload = payload or {}

    change_request = ChangeRequest.objects.create(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        status=ChangeRequest.STATUS_PENDING,
    )
    change_request.request_hash = _request_hash_for_payload(payload)
    change_request.save(update_fields=["request_hash"])

    if uploaded_files:
        _save_queued_files(change_request, uploaded_files)

    return change_request


def queue_place_create(**fields):
    uploaded_files = fields.pop("files", [])
    payload = {
        "title": fields.get("title") or "",
        "description": fields.get("description") or "",
        "latitude": fields.get("latitude"),
        "longitude": fields.get("longitude"),
        "start_date": fields.get("start_date"),
        "end_date": fields.get("end_date"),
    }
    return _queue_request(
        action=ChangeRequest.ACTION_CREATE,
        entity_type=ChangeRequest.ENTITY_PLACE,
        entity_id=None,
        payload=payload,
        uploaded_files=uploaded_files,
    )


def queue_place_update(place, **fields):
    uploaded_files = fields.pop("files", [])
    payload = {
        "place_id": place.pk,
        "title": fields.get("title") or "",
        "description": fields.get("description") or "",
        "latitude": fields.get("latitude"),
        "longitude": fields.get("longitude"),
        "start_date": fields.get("start_date"),
        "end_date": fields.get("end_date"),
    }
    return _queue_request(
        action=ChangeRequest.ACTION_UPDATE,
        entity_type=ChangeRequest.ENTITY_PLACE,
        entity_id=place.pk,
        payload=payload,
        uploaded_files=uploaded_files,
    )


def queue_place_delete(place):
    payload = {
        "place_id": place.pk,
        "title": place.title,
        "description": place.description,
    }
    return _queue_request(
        action=ChangeRequest.ACTION_DELETE,
        entity_type=ChangeRequest.ENTITY_PLACE,
        entity_id=place.pk,
        payload=payload,
        uploaded_files=None,
    )


def queue_photo_delete(photo):
    payload = {
        "photo_id": photo.pk,
        "place_id": photo.place_id,
        "original_filename": photo.original_filename,
    }
    return _queue_request(
        action=ChangeRequest.ACTION_DELETE,
        entity_type=ChangeRequest.ENTITY_PHOTO,
        entity_id=photo.pk,
        payload=payload,
        uploaded_files=None,
    )


def build_change_request_message(change_request):
    payload = change_request.payload or {}
    action_label = change_request.get_action_display()

    parts = [
        f"#{change_request.pk}",
        action_label,
        f"entity: {change_request.entity_type}",
    ]

    if change_request.action == ChangeRequest.ACTION_CREATE:
        parts.append(f"title: {payload.get('title') or '—'}")
    elif change_request.action == ChangeRequest.ACTION_UPDATE:
        parts.append(f"place_id: {change_request.entity_id}")
        parts.append(f"title: {payload.get('title') or '—'}")
    elif change_request.action == ChangeRequest.ACTION_DELETE:
        parts.append(f"entity_id: {change_request.entity_id}")
        if change_request.entity_type == ChangeRequest.ENTITY_PLACE:
            parts.append(f"title: {payload.get('title') or '—'}")
        else:
            parts.append(f"filename: {payload.get('original_filename') or '—'}")

    if payload.get("files"):
        parts.append(f"files: {len(payload.get('files'))}")

    return "\n".join(parts)


def _apply_pending_action(change_request, reviewed_by):
    payload = change_request.payload or {}
    files = _load_queued_files(change_request)

    if change_request.action == ChangeRequest.ACTION_CREATE:
        place = create_place(
            title=payload.get("title") or "",
            description=payload.get("description") or "",
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            files=files,
        )
        change_request.entity_id = place.pk
        change_request.save(update_fields=["entity_id"])
        return place

    if change_request.action == ChangeRequest.ACTION_UPDATE:
        place = Place.objects.get(pk=change_request.entity_id)
        update_place(
            place,
            title=payload.get("title") or "",
            description=payload.get("description") or "",
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            files=files,
        )
        return place

    if change_request.action == ChangeRequest.ACTION_DELETE:
        if change_request.entity_type == ChangeRequest.ENTITY_PLACE:
            place = Place.objects.get(pk=change_request.entity_id)
            delete_place(place)
        elif change_request.entity_type == ChangeRequest.ENTITY_PHOTO:
            photo = Photo.objects.get(pk=change_request.entity_id)
            delete_photo(photo)
        else:
            raise ServiceOperationError(f"Неизвестный тип сущности: {change_request.entity_type}")
        return None

    raise ServiceOperationError(f"Неизвестный тип действия: {change_request.action}")


def approve_change_request(change_request_id, reviewed_by="system"):
    change_request = ChangeRequest.objects.get(pk=change_request_id)

    if change_request.status != ChangeRequest.STATUS_PENDING:
        raise ServiceOperationError(
            f"Заявка #{change_request.pk} уже обработана: {change_request.status}"
        )

    try:
        _apply_pending_action(change_request, reviewed_by)
        change_request.status = ChangeRequest.STATUS_APPROVED
        change_request.reviewed_at = timezone.now()
        change_request.reviewed_by = reviewed_by
        change_request.error_message = ""
        change_request.save(update_fields=["status", "reviewed_at", "reviewed_by", "error_message", "updated_at"])
        _cleanup_change_request_dir(change_request)
        return change_request
    except Exception as exc:
        change_request.status = ChangeRequest.STATUS_FAILED
        change_request.reviewed_at = timezone.now()
        change_request.reviewed_by = reviewed_by
        change_request.error_message = str(exc)
        change_request.save(update_fields=["status", "reviewed_at", "reviewed_by", "error_message", "updated_at"])
        _cleanup_change_request_dir(change_request)
        raise ServiceOperationError(f"Ошибка применения заявки #{change_request.pk}: {exc}") from exc


def reject_change_request(change_request_id, reviewed_by="system"):
    change_request = ChangeRequest.objects.get(pk=change_request_id)

    if change_request.status != ChangeRequest.STATUS_PENDING:
        raise ServiceOperationError(
            f"Заявка #{change_request.pk} уже обработана: {change_request.status}"
        )

    change_request.status = ChangeRequest.STATUS_REJECTED
    change_request.reviewed_at = timezone.now()
    change_request.reviewed_by = reviewed_by
    change_request.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
    _cleanup_change_request_dir(change_request)
    return change_request