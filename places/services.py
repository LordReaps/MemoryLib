import logging
import uuid

from django.db import transaction
from io import BytesIO
from PIL import Image, ImageOps
from django.core.files.base import ContentFile
from django.urls import reverse

from .models import Photo, Place

logger = logging.getLogger(__name__)


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
    place = _build_place(**fields)
    place.save()
    return place


@transaction.atomic
def update_place(place, **fields):
    place.title = fields["title"]
    place.description = fields.get("description") or ""
    place.latitude = fields["latitude"]
    place.longitude = fields["longitude"]
    place.start_date = fields.get("start_date")
    place.end_date = fields.get("end_date")
    place.full_clean()
    place.save()
    return place


@transaction.atomic
def delete_place(place):
    files = _stored_files(place.photos.all())
    place.delete()
    transaction.on_commit(lambda: _delete_stored_files(files))


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
            sort_order=sort_order
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
    except Exception as e:
        # Выводим ошибку в консоль и пробрасываем дальше, чтобы сервер вернул 500 и мы увидели причину
        print(f"!!! ОШИБКА ПРИ ОБРАБОТКЕ {uploaded_file.name}: {e}")
        raise e
    return None


def add_photos_to_place(place_id, files):
    photos = []
    # Находим максимальный sort_order для этого места
    from django.db.models import Max
    max_order = Photo.objects.filter(place_id=place_id).aggregate(Max('sort_order'))['sort_order__max'] or 0

    for index, file in enumerate(files):
        # Передаем порядок с учетом существующих
        photo = _process_image(file, place_id, max_order + index + 1)
        if photo:
            photos.append(photo)
    return photos


def _stored_files(photos):
    """Собирает пары (хранилище, имя файла) до удаления записей из БД."""
    files = []
    for photo in photos:
        for field in (photo.image, photo.thumbnail):
            if field and field.name:
                files.append((field.storage, field.name))
    return files


def _delete_stored_files(files):
    """Удаляет файлы с диска, не прерывая работу из-за одной ошибки."""
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception:
            logger.exception("Не удалось удалить файл %s", name)


def delete_photo_files(photo):
    """Удаляет физические файлы фото с диска."""
    _delete_stored_files(_stored_files([photo]))


@transaction.atomic
def delete_photo(photo):
    """Удаляет запись фото и его файлы после успешного коммита."""
    files = _stored_files([photo])
    photo.delete()
    transaction.on_commit(lambda: _delete_stored_files(files))
