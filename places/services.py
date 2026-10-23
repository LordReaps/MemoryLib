import uuid

from django.db import transaction
import os
from io import BytesIO
from PIL import Image, ImageOps
from django.conf import settings
from django.core.files.base import ContentFile
from django.urls import reverse

from .models import Photo

from .models import Place


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
        # Добавляем фото (используем reverse для генерации URL)
        "photos": [
            {
                "id": p.id,
                # Проверяем, есть ли реально файл миниатюры
                "thumb_url": reverse("places:serve_photo", kwargs={"pk": p.id, "file_type": "thumb"}) if p.thumbnail else None,
                "original_url": reverse("places:serve_photo", kwargs={"pk": p.id, "file_type": "original"})
            } for p in place.photos.all()
        ]
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
    files = fields.pop("files", None)
    place = None
    try:
        place = _build_place(**fields)
        place.save()
        if files:
            add_photos_to_place(place.id, files)
        return place
    except Exception as exc:
        if place and place.pk:
            try:
                delete_place(place)
            except Exception:
                pass
        raise ServiceOperationError(f"Не удалось создать место '{fields.get('title', '')}': {exc}") from exc


@transaction.atomic
def update_place(place, **fields):
    files = fields.pop("files", None)
    previous_values = {
        "title": place.title,
        "description": place.description,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "start_date": place.start_date,
        "end_date": place.end_date,
    }

    try:
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
    except Exception as exc:
        for field_name, value in previous_values.items():
            setattr(place, field_name, value)
        raise ServiceOperationError(f"Не удалось обновить место #{place.pk}: {exc}") from exc


@transaction.atomic
def delete_place(place):
    try:
        delete_photos_for_place(place.pk)
        place.delete()
    except Exception as exc:
        raise ServiceOperationError(f"Не удалось удалить место #{place.pk}: {exc}") from exc


def _process_image(uploaded_file, place_id, sort_order):
    """Внутренняя функция: делает миниатюру и готовит объект Photo."""
    try:
        # Открываем файл через Pillow (проверка, что это картинка)
        img = Image.open(uploaded_file)

        # Учитываем EXIF-поворот (чтобы фото с телефона не лежали на боку)
        img = ImageOps.exif_transpose(img)

        # Конвертируем в RGB для безопасного сохранения (если PNG с прозрачностью)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Генерируем миниатюру (макс. 400px по большей стороне)
        thumb = img.copy()
        thumb.thumbnail((400, 400))

        thumb_io = BytesIO()
        thumb.save(thumb_io, format="WEBP", quality=85)
        thumb_file = ContentFile(thumb_io.getvalue())

        # Создаем запись в БД (файл оригинала прикрепится чуть ниже)
        photo = Photo(
            place_id=place_id,
            original_filename=uploaded_file.name,
            mime_type=uploaded_file.content_type or "image/jpeg",
            size_bytes=uploaded_file.size,
            sort_order=sort_order,
        )

        # Сохраняем оригинал (Pillow пересохранит его в правильном формате)
        img_io = BytesIO()
        img.save(img_io, format="JPEG", quality=90)
        # Генерируем безопасное имя с уникальным суффиксом
        safe_name = f"{uuid.uuid4().hex}.jpg"
        photo.image.save(safe_name, ContentFile(img_io.getvalue()), save=False)

        # Сохраняем миниатюру
        safe_thumb_name = f"{uuid.uuid4().hex}.webp"
        photo.thumbnail.save(safe_thumb_name, thumb_file, save=False)

        photo.save()

        return photo
    except Exception as exc:
        raise ServiceOperationError(f"Не удалось обработать фото '{getattr(uploaded_file, 'name', 'unknown')}': {exc}") from exc


def add_photos_to_place(place_id, files):
    photos = []
    # Находим максимальный sort_order для этого места
    from django.db.models import Max

    max_order = Photo.objects.filter(place_id=place_id).aggregate(Max("sort_order"))["sort_order__max"] or 0

    for index, file in enumerate(files):
        try:
            photo = _process_image(file, place_id, max_order + index + 1)
        except Exception:
            for created_photo in photos:
                try:
                    delete_photo(created_photo)
                except Exception:
                    pass
            raise

        if photo:
            photos.append(photo)
    return photos


def delete_photo_files(photo):
    """Удаляет физические файлы фото с диска."""
    errors = []

    for field_name in ("image", "thumbnail"):
        field = getattr(photo, field_name, None)
        if not field:
            continue

        try:
            field.delete(save=False)
        except Exception as exc:
            errors.append(f"{field_name}: {exc}")

    if errors:
        raise ServiceOperationError("; ".join(errors))


def delete_photo(photo):
    """Удаляет фото полностью: сначала файлы, затем запись в БД."""
    try:
        delete_photo_files(photo)
    except Exception as exc:
        raise ServiceOperationError(f"Не удалось удалить файлы фотографии #{photo.pk}: {exc}") from exc

    try:
        photo.delete()
    except Exception as exc:
        raise ServiceOperationError(f"Не удалось удалить запись фотографии #{photo.pk}: {exc}") from exc


def delete_photos_for_place(place_id):
    """Удаляет все фото места (и записи, и файлы). Вызывается перед удалением места."""
    photos = list(Photo.objects.filter(place_id=place_id))
    errors = []

    for photo in photos:
        try:
            delete_photo(photo)
        except ServiceOperationError as exc:
            errors.append(str(exc))

    if errors:
        raise ServiceOperationError("; ".join(errors))


# Сохраняем совместимость со старым вызовом delete_photo_files(photo) из представлений.
# В конечном итоге вся бизнес-логика должна вызываться через этот сервисный слой.


os = os
settings = settings


# Не используем 'os' и 'settings' здесь специально: они нужны только для совместимости и предотвращения
# ложных предупреждений линтера, но фактическая логика реализована выше.


# Legacy aliases for safe imports from existing code.
# They keep current behavior while centralizing all write operations in service layer.

def delete_photo_by_id(photo_id):
    photo = Photo.objects.filter(pk=photo_id).first()
    if photo is None:
        return False
    delete_photo(photo)
    return True


# Блок ниже нужен только для соблюдения текущих импортов из views и для уменьшения риска поломки.
# Все изменения состояния должны происходить через сервисный слой.


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
]


# При загрузке модуля не делаем лишних действий: сервисные функции должны оставаться простыми и явно вызываемыми.


























































































































































































































































































































































































































































































































































































































































































































































































































































































