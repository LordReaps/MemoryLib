import os
import shutil
import tempfile
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import ChangeRequest, Folder, Photo, Place
from .services import (
    approve_change_request,
    build_change_request_message,
    create_place,
    delete_photo,
    delete_place,
    queue_place_create,
    queue_place_update,
    update_place,
)
from .telegram_bot import (
    _prepare_telegram_photo,
    _request_photos,
    handle_callback,
    send_pending_requests,
)
from .views import _photo_content_type


class AuthenticatedTestCase(TestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["authenticated"] = True
        session.save()


class PlaceModelTests(TestCase):
    def test_create_place(self):
        place = Place.objects.create(
            title="Париж",
            description="Первое совместное путешествие",
            latitude=48.8566,
            longitude=2.3522,
            start_date="2024-06-01",
            end_date="2024-06-07",
        )
        self.assertEqual(place.title, "Париж")
        self.assertEqual(Place.objects.count(), 1)

    def test_rejects_invalid_latitude(self):
        place = Place(
            title="Неверная широта",
            latitude=91.0,
            longitude=10.0,
        )
        with self.assertRaises(ValidationError):
            place.full_clean()

    def test_rejects_invalid_longitude(self):
        place = Place(
            title="Неверная долгота",
            latitude=10.0,
            longitude=181.0,
        )
        with self.assertRaises(ValidationError):
            place.full_clean()

    def test_rejects_end_date_before_start_date(self):
        place = Place(
            title="Неверные даты",
            latitude=10.0,
            longitude=10.0,
            start_date="2024-06-10",
            end_date="2024-06-01",
        )
        with self.assertRaises(ValidationError):
            place.full_clean()

    def test_allows_empty_description_and_dates(self):
        place = Place(
            title="Только координаты",
            latitude=55.7558,
            longitude=37.6173,
        )
        place.full_clean()
        place.save()
        self.assertEqual(place.description, "")


class PlaceServiceTests(TestCase):
    def test_create_place(self):
        place = create_place(
            title="Рим",
            description="",
            latitude=41.9,
            longitude=12.5,
            start_date=None,
            end_date=None,
        )
        self.assertEqual(Place.objects.get(pk=place.pk).title, "Рим")

    def test_update_place(self):
        place = create_place(
            title="Рим",
            latitude=41.9,
            longitude=12.5,
        )
        updated = update_place(
            place,
            title="Рим обновлённый",
            description="Форум",
            latitude=41.89,
            longitude=12.49,
            start_date=None,
            end_date=None,
        )
        self.assertEqual(updated.title, "Рим обновлённый")
        self.assertEqual(updated.description, "Форум")
        self.assertEqual(Place.objects.count(), 1)

    def test_delete_place(self):
        place = create_place(title="Рим", latitude=41.9, longitude=12.5)
        delete_place(place)
        self.assertEqual(Place.objects.count(), 0)

    def test_failed_queue_does_not_leave_change_request(self):
        upload = SimpleUploadedFile("photo.jpg", b"not an image", content_type="image/jpeg")

        with patch("places.services._save_queued_files", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                queue_place_create(
                    title="Сбой",
                    latitude=1.0,
                    longitude=2.0,
                    files=[upload],
                )

        self.assertEqual(ChangeRequest.objects.count(), 0)

    def test_create_rejects_invalid_coordinates(self):
        with self.assertRaises(ValidationError):
            create_place(title="За краем", latitude=95.0, longitude=10.0)


class MapViewTests(AuthenticatedTestCase):
    def test_map_view_includes_places_data(self):
        Place.objects.create(
            title="Берлин",
            latitude=52.52,
            longitude=13.405,
        )
        response = self.client.get(reverse("places:map"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "places-data")
        self.assertContains(response, '"latitude": 52.52')
        self.assertContains(response, '"longitude": 13.405')
        self.assertContains(response, "add-place-btn")
        self.assertContains(response, 'id="place-sheet"')
        self.assertContains(response, 'id="lightbox-prev"')
        self.assertContains(response, 'id="photo-preview"')
        self.assertContains(response, 'id="id_folder"')
        self.assertContains(response, "map-home-link")
        self.assertContains(response, "detail-url-template")


class HomePageTests(AuthenticatedTestCase):
    def test_home_renders_sections(self):
        response = self.client.get(reverse("places:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Наш Хаб")
        self.assertContains(response, "Наша история")
        self.assertContains(response, "Наши папки")
        self.assertContains(response, "Недавно добавленные")
        self.assertContains(response, "Карта")
        self.assertContains(response, "Создать папку")
        self.assertNotContains(response, 'id="map"')

    def test_home_requires_auth(self):
        response = Client().get(reverse("places:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_home_stats_counts(self):
        folder = Folder.objects.create(name="Анапа2024")
        Place.objects.create(title="Место", latitude=1.0, longitude=1.0, folder=folder)
        Photo.objects.create(
            place=Place.objects.get(),
            image="places/a.jpg",
            thumbnail="places/b.webp",
            original_filename="b.jpg",
        )

        response = self.client.get(reverse("places:home"))

        self.assertEqual(response.context["places_count"], 1)
        self.assertEqual(response.context["folders_count"], 1)
        self.assertEqual(response.context["photos_count"], 1)

    def test_home_lists_places_and_folders(self):
        folder = Folder.objects.create(name="Анапа2024")
        place = Place.objects.create(
            title="Пляж",
            latitude=44.89,
            longitude=37.32,
            folder=folder,
            start_date="2024-08-01",
            end_date="2024-08-10",
        )

        response = self.client.get(reverse("places:home"))

        self.assertContains(response, "Пляж")
        self.assertContains(response, "Анапа2024")
        self.assertContains(response, "0 фото")
        self.assertContains(response, 'class="folder-card"')
        self.assertContains(response, 'folder-card__badge">1<')
        self.assertContains(
            response,
            reverse("places:folder_detail", kwargs={"pk": folder.pk}),
        )
        self.assertContains(
            response,
            reverse("places:place_detail", kwargs={"pk": place.pk}),
        )

    def test_folder_card_uses_first_place_photo(self):
        folder = Folder.objects.create(name="Обложка")
        place = Place.objects.create(
            title="Первое место",
            latitude=1.0,
            longitude=1.0,
            folder=folder,
        )
        photo = Photo.objects.create(
            place=place,
            image="places/a.jpg",
            thumbnail="places/b.webp",
            original_filename="b.jpg",
        )

        response = self.client.get(reverse("places:home"))

        self.assertContains(
            response,
            reverse("places:serve_photo", kwargs={"pk": photo.pk, "file_type": "thumb"}),
        )

    def test_folder_card_without_places_has_placeholder(self):
        Folder.objects.create(name="Пустая")

        response = self.client.get(reverse("places:home"))

        self.assertContains(response, "folder-card__cover-empty")

    def test_home_shows_first_photo_by_sort_order(self):
        place = Place.objects.create(title="Фото", latitude=1.0, longitude=1.0)
        later = Photo.objects.create(
            place=place,
            image="places/a.jpg",
            thumbnail="places/b.webp",
            original_filename="b.jpg",
            sort_order=2,
        )
        first = Photo.objects.create(
            place=place,
            image="places/c.jpg",
            thumbnail="places/d.webp",
            original_filename="d.jpg",
            sort_order=1,
        )

        response = self.client.get(reverse("places:home"))

        self.assertContains(response, f"/photos/{first.pk}/thumb/")
        self.assertNotContains(response, f"/photos/{later.pk}/thumb/")

    def test_home_shows_ten_newest_places(self):
        now = timezone.now()
        for index in range(11):
            place = Place.objects.create(
                title=f"Место {index}",
                latitude=1.0,
                longitude=1.0,
            )
            Place.objects.filter(pk=place.pk).update(
                created_at=now - timedelta(days=index)
            )

        response = self.client.get(reverse("places:home"))
        recent = list(response.context["recent_places"])

        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0].title, "Место 0")
        self.assertEqual(recent[-1].title, "Место 9")

    def test_home_empty_states(self):
        response = self.client.get(reverse("places:home"))
        self.assertContains(response, "Пока нет записей.")
        self.assertContains(response, "Пока нет папок.")


class FolderCreateViewTests(AuthenticatedTestCase):
    def test_create_folder(self):
        response = self.client.post(
            reverse("places:folder_create"),
            {"name": "Анапа2024"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["folder"]["name"], "Анапа2024")
        self.assertTrue(Folder.objects.filter(name="Анапа2024").exists())

    def test_empty_name_rejected(self):
        response = self.client.post(
            reverse("places:folder_create"),
            {"name": "   "},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Folder.objects.exists())

    def test_duplicate_name_rejected(self):
        Folder.objects.create(name="Анапа2024")
        response = self.client.post(
            reverse("places:folder_create"),
            {"name": "анапа2024"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Folder.objects.count(), 1)

    def test_get_not_allowed(self):
        response = self.client.get(reverse("places:folder_create"))
        self.assertEqual(response.status_code, 405)

    def test_requires_auth(self):
        response = Client().post(reverse("places:folder_create"), {"name": "Тест"})
        self.assertEqual(response.status_code, 302)


class FolderDetailTests(AuthenticatedTestCase):
    def test_shows_only_places_of_this_folder(self):
        folder = Folder.objects.create(name="Анапа2024")
        other = Folder.objects.create(name="Сочи")
        Place.objects.create(title="Пляж", latitude=1.0, longitude=1.0, folder=folder)
        Place.objects.create(title="Горы", latitude=1.0, longitude=1.0, folder=other)

        response = self.client.get(
            reverse("places:folder_detail", kwargs={"pk": folder.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Анапа2024")
        self.assertContains(response, "Пляж")
        self.assertNotContains(response, "Горы")
        self.assertContains(response, "Записей: 1")
        self.assertContains(
            response,
            reverse("places:folder_delete", kwargs={"pk": folder.pk}),
        )

    def test_empty_folder_message(self):
        folder = Folder.objects.create(name="Пустая")

        response = self.client.get(
            reverse("places:folder_detail", kwargs={"pk": folder.pk})
        )

        self.assertContains(response, "В этой папке пока нет мест.")

    def test_missing_folder_returns_404(self):
        response = self.client.get(
            reverse("places:folder_detail", kwargs={"pk": 999999})
        )
        self.assertEqual(response.status_code, 404)

    def test_requires_auth(self):
        folder = Folder.objects.create(name="Тест")

        response = Client().get(
            reverse("places:folder_detail", kwargs={"pk": folder.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class FolderDeleteTests(AuthenticatedTestCase):
    def test_delete_keeps_places_without_folder(self):
        folder = Folder.objects.create(name="Анапа2024")
        place = Place.objects.create(
            title="Пляж",
            latitude=1.0,
            longitude=1.0,
            folder=folder,
        )

        response = self.client.post(
            reverse("places:folder_delete", kwargs={"pk": folder.pk})
        )

        self.assertRedirects(response, reverse("places:home"))
        self.assertFalse(Folder.objects.filter(pk=folder.pk).exists())
        place.refresh_from_db()
        self.assertIsNone(place.folder_id)
        self.assertTrue(Place.objects.filter(pk=place.pk).exists())

    def test_get_not_allowed(self):
        folder = Folder.objects.create(name="Тест")

        response = self.client.get(
            reverse("places:folder_delete", kwargs={"pk": folder.pk})
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Folder.objects.filter(pk=folder.pk).exists())

    def test_requires_auth(self):
        folder = Folder.objects.create(name="Тест")

        response = Client().post(
            reverse("places:folder_delete", kwargs={"pk": folder.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Folder.objects.filter(pk=folder.pk).exists())

    def test_missing_folder_returns_404(self):
        response = self.client.post(
            reverse("places:folder_delete", kwargs={"pk": 999999})
        )
        self.assertEqual(response.status_code, 404)


class FolderListTests(AuthenticatedTestCase):
    def test_lists_all_folders(self):
        first = Folder.objects.create(name="Анапа2024")
        Folder.objects.create(name="Сочи")

        response = self.client.get(reverse("places:folder_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Наши папки")
        self.assertContains(response, "Анапа2024")
        self.assertContains(response, "Сочи")
        self.assertContains(
            response,
            reverse("places:folder_detail", kwargs={"pk": first.pk}),
        )

    def test_empty_state(self):
        response = self.client.get(reverse("places:folder_list"))
        self.assertContains(response, "Пока нет папок.")

    def test_requires_auth(self):
        response = Client().get(reverse("places:folder_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class PlaceDetailTests(AuthenticatedTestCase):
    def test_shows_all_place_fields(self):
        folder = Folder.objects.create(name="Анапа2024")
        place = Place.objects.create(
            title="Пляж",
            description="Тёплое море",
            latitude=44.89,
            longitude=37.32,
            folder=folder,
            start_date="2024-08-01",
            end_date="2024-08-10",
        )

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": place.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пляж")
        self.assertContains(response, "Тёплое море")
        self.assertContains(response, "1 августа 2024")
        self.assertContains(response, "10 августа 2024")
        self.assertContains(response, "Анапа2024")
        self.assertContains(
            response,
            reverse("places:folder_detail", kwargs={"pk": folder.pk}),
        )
        self.assertContains(response, f"?place={place.pk}")

    def test_shows_all_photos(self):
        place = Place.objects.create(title="Фото", latitude=1.0, longitude=1.0)
        first = Photo.objects.create(
            place=place,
            image="places/a.jpg",
            thumbnail="places/b.webp",
            original_filename="b.jpg",
        )
        second = Photo.objects.create(
            place=place,
            image="places/c.jpg",
            thumbnail="places/d.webp",
            original_filename="d.jpg",
        )

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": place.pk})
        )

        self.assertContains(
            response, reverse("places:serve_photo", kwargs={"pk": first.pk, "file_type": "thumb"})
        )
        self.assertContains(
            response, reverse("places:serve_photo", kwargs={"pk": second.pk, "file_type": "thumb"})
        )
        self.assertContains(response, 'id="lightbox"')
        self.assertContains(response, "place-gallery.js")
        self.assertNotContains(response, 'target="_blank"')

    def test_without_photos_and_folder(self):
        place = Place.objects.create(title="Пусто", latitude=1.0, longitude=1.0)

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": place.pk})
        )

        self.assertContains(response, "Фотографий пока нет.")
        self.assertContains(response, "0 фото")

    def test_missing_place_returns_404(self):
        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": 999999})
        )
        self.assertEqual(response.status_code, 404)

    def test_requires_auth(self):
        place = Place.objects.create(title="Тест", latitude=1.0, longitude=1.0)

        response = Client().get(
            reverse("places:place_detail", kwargs={"pk": place.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def _place(self, title, **fields):
        return Place.objects.create(title=title, latitude=1.0, longitude=1.0, **fields)

    def test_similar_shows_same_folder_first(self):
        folder = Folder.objects.create(name="Анапа2024")
        other_folder = Folder.objects.create(name="Сочи")
        current = self._place("Текущее", folder=folder, start_date="2024-08-01")
        same_folder = self._place("Из папки", folder=folder)
        by_date = self._place("По дате", folder=other_folder, start_date="2024-08-03")

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": current.pk})
        )
        similar = list(response.context["similar_places"])

        self.assertEqual([place.pk for place in similar], [same_folder.pk, by_date.pk])
        self.assertContains(response, "Похожие")
        self.assertContains(response, "Из папки")
        self.assertContains(response, "По дате")

    def test_similar_uses_date_window_and_distance_order(self):
        current = self._place("Текущее", start_date="2024-08-01")
        near = self._place("Рядом", start_date="2024-08-03")
        middle = self._place("Средне", start_date="2024-07-27")
        far_ok = self._place("Далеко", start_date="2024-08-11")
        too_far = self._place("Слишком далеко", start_date="2024-08-20")

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": current.pk})
        )
        similar = list(response.context["similar_places"])

        self.assertEqual(
            [place.pk for place in similar],
            [near.pk, middle.pk, far_ok.pk],
        )
        self.assertNotIn(too_far.pk, [place.pk for place in similar])

    def test_similar_limited_to_four(self):
        folder = Folder.objects.create(name="Большая")
        current = self._place("Текущее", folder=folder)
        for index in range(6):
            self._place(f"Сосед {index}", folder=folder)

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": current.pk})
        )

        self.assertEqual(len(response.context["similar_places"]), 4)

    def test_similar_hidden_when_nothing_matches(self):
        current = self._place("Одинокое")
        self._place("Без дат", start_date=None)

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": current.pk})
        )

        self.assertEqual(list(response.context["similar_places"]), [])
        self.assertNotContains(response, "Похожие")

    def test_similar_excludes_current_place(self):
        folder = Folder.objects.create(name="Одна")
        current = self._place("Текущее", folder=folder)

        response = self.client.get(
            reverse("places:place_detail", kwargs={"pk": current.pk})
        )

        self.assertNotIn(current.pk, [place.pk for place in response.context["similar_places"]])


@override_settings(ENABLE_TG_CONFIRMATION=False)
class PlaceViewTests(AuthenticatedTestCase):
    def test_create_place_via_post(self):
        response = self.client.post(
            reverse("places:place_create"),
            {
                "title": "Лиссабон",
                "description": "Океан",
                "latitude": "38.7223",
                "longitude": "-9.1393",
                "start_date": "2023-05-01",
                "end_date": "2023-05-05",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(Place.objects.count(), 1)
        self.assertEqual(data["place"]["title"], "Лиссабон")

    def test_create_place_with_folder(self):
        folder = Folder.objects.create(name="Анапа2024")
        response = self.client.post(
            reverse("places:place_create"),
            {
                "title": "Лиссабон",
                "latitude": "38.7223",
                "longitude": "-9.1393",
                "folder": str(folder.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["place"]["folder"], {"id": folder.pk, "name": "Анапа2024"})
        self.assertEqual(Place.objects.get().folder_id, folder.pk)

    def test_create_returns_json_error_when_saving_fails(self):
        with patch("places.views.create_place", side_effect=OSError("boom")):
            response = self.client.post(
                reverse("places:place_create"),
                {"title": "Сбой", "latitude": "1", "longitude": "2"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(Place.objects.count(), 0)

    def test_update_place_via_post(self):
        place = create_place(title="Лиссабон", latitude=38.72, longitude=-9.14)
        response = self.client.post(
            reverse("places:place_update", kwargs={"pk": place.pk}),
            {
                "title": "Лиссабон центр",
                "description": "",
                "latitude": "38.713",
                "longitude": "-9.139",
            },
        )
        self.assertEqual(response.status_code, 200)
        place.refresh_from_db()
        self.assertEqual(place.title, "Лиссабон центр")
        self.assertAlmostEqual(place.latitude, 38.713, places=3)

    def test_delete_place_via_post(self):
        place = create_place(title="Лиссабон", latitude=38.72, longitude=-9.14)
        response = self.client.post(
            reverse("places:place_delete", kwargs={"pk": place.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Place.objects.count(), 0)

    def test_create_rejects_invalid_latitude(self):
        response = self.client.post(
            reverse("places:place_create"),
            {
                "title": "Неверная широта",
                "latitude": "91",
                "longitude": "10",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(Place.objects.count(), 0)

    def test_create_rejects_end_date_before_start_date(self):
        response = self.client.post(
            reverse("places:place_create"),
            {
                "title": "Неверные даты",
                "latitude": "10",
                "longitude": "10",
                "start_date": "2024-06-10",
                "end_date": "2024-06-01",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("end_date", response.json()["errors"])
        self.assertEqual(Place.objects.count(), 0)

    def test_create_rejects_get(self):
        response = self.client.get(reverse("places:place_create"))
        self.assertEqual(response.status_code, 405)


@override_settings(
    ENABLE_TG_CONFIRMATION=True,
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_ADMIN_CHAT_ID="12345",
)
class PlaceApprovalViewTests(AuthenticatedTestCase):
    def test_create_is_queued_until_approved(self):
        response = self.client.post(
            reverse("places:place_create"),
            {
                "title": "Лиссабон",
                "description": "Океан",
                "latitude": "38.7223",
                "longitude": "-9.1393",
                "start_date": "2023-05-01",
                "end_date": "2023-05-05",
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "pending")
        self.assertIn("рассмотрение", data["message"])
        self.assertEqual(Place.objects.count(), 0)

        change_request = ChangeRequest.objects.get(pk=data["change_request_id"])
        self.assertEqual(change_request.payload["start_date"], "2023-05-01")
        self.assertEqual(change_request.payload["end_date"], "2023-05-05")

        approve_change_request(change_request.pk, reviewed_by="test")
        place = Place.objects.get()
        self.assertEqual(place.title, "Лиссабон")
        self.assertEqual(place.start_date.isoformat(), "2023-05-01")
        self.assertEqual(place.end_date.isoformat(), "2023-05-05")

    def test_create_with_folder_is_queued_until_approved(self):
        folder = Folder.objects.create(name="Анапа2024")
        response = self.client.post(
            reverse("places:place_create"),
            {
                "title": "Анапа",
                "latitude": "44.89",
                "longitude": "37.32",
                "folder": str(folder.pk),
            },
        )

        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "pending")
        self.assertEqual(Place.objects.count(), 0)

        change_request = ChangeRequest.objects.get(pk=data["change_request_id"])
        self.assertEqual(change_request.payload["folder_id"], folder.pk)
        self.assertEqual(change_request.payload["folder_name"], "Анапа2024")

        approve_change_request(change_request.pk, reviewed_by="test")
        place = Place.objects.get()
        self.assertEqual(place.folder_id, folder.pk)

    def test_queue_failure_returns_json_and_creates_no_request(self):
        upload = SimpleUploadedFile("photo.jpg", b"not an image", content_type="image/jpeg")

        with patch("places.services._save_queued_files", side_effect=OSError("boom")):
            response = self.client.post(
                reverse("places:place_create"),
                {"title": "Сбой", "latitude": "1", "longitude": "2", "photos": upload},
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(ChangeRequest.objects.count(), 0)
        self.assertEqual(Place.objects.count(), 0)

    def test_update_is_queued_until_approved(self):
        place = create_place(
            title="Лиссабон",
            latitude=38.72,
            longitude=-9.14,
        )

        response = self.client.post(
            reverse("places:place_update", kwargs={"pk": place.pk}),
            {
                "title": "Лиссабон центр",
                "description": "Площадь",
                "latitude": "38.713",
                "longitude": "-9.139",
                "start_date": "2023-05-01",
                "end_date": "2023-05-05",
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "pending")
        place.refresh_from_db()
        self.assertEqual(place.title, "Лиссабон")

        approve_change_request(data["change_request_id"], reviewed_by="test")
        place.refresh_from_db()
        self.assertEqual(place.title, "Лиссабон центр")
        self.assertEqual(place.start_date.isoformat(), "2023-05-01")
        self.assertEqual(place.end_date.isoformat(), "2023-05-05")

    def test_telegram_message_has_requested_fields_and_order(self):
        change_request = ChangeRequest.objects.create(
            action=ChangeRequest.ACTION_CREATE,
            entity_type=ChangeRequest.ENTITY_PLACE,
            payload={
                "title": "Лиссабон",
                "description": "Вид на океан",
            },
        )

        message = build_change_request_message(change_request, file_count=2)

        self.assertEqual(
            message.splitlines(),
            [
                "Пришло изменение",
                f"#{change_request.pk}",
                "Создание",
                "entity: place",
                "title: Лиссабон",
                "Описание: Вид на океан",
                "Папка: —",
                "Количество файлов: 2",
            ],
        )


class TelegramPhotoTests(TestCase):
    def setUp(self):
        super().setUp()
        self.media_dir = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_dir)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)
        super().tearDown()

    def _image_upload(self, name, image_format):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "blue").save(buffer, format=image_format)
        content_type = "image/png" if image_format == "PNG" else "image/jpeg"
        return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)

    def test_update_notification_includes_existing_and_new_photos(self):
        place = create_place(
            title="Рим",
            latitude=41.9,
            longitude=12.5,
            files=[self._image_upload("existing.jpg", "JPEG")],
        )
        change_request = queue_place_update(
            place,
            title="Рим",
            description="",
            latitude=41.9,
            longitude=12.5,
            files=[self._image_upload("new.png", "PNG")],
        )

        photos = _request_photos(change_request)

        self.assertEqual([name for name, _ in photos], ["existing.jpg", "new.png"])
        self.assertEqual(len(photos), 2)
        self.assertTrue(all(content for _, content in photos))

    def test_telegram_photo_is_normalized_to_jpeg(self):
        upload = self._image_upload("upload.png", "PNG")

        filename, content = _prepare_telegram_photo(upload.name, upload.read())

        self.assertEqual(filename, "upload.jpg")
        with Image.open(BytesIO(content)) as image:
            self.assertEqual(image.format, "JPEG")

    def test_already_notified_request_sends_photos_as_reply(self):
        place = create_place(
            title="Рим",
            latitude=41.9,
            longitude=12.5,
            files=[self._image_upload("existing.jpg", "JPEG")],
        )
        change_request = queue_place_update(
            place,
            title="Рим",
            description="",
            latitude=41.9,
            longitude=12.5,
        )
        change_request.telegram_chat_id = "12345"
        change_request.telegram_message_id = "67890"
        change_request.save(update_fields=["telegram_chat_id", "telegram_message_id"])

        bot = SimpleNamespace(send_photo=AsyncMock(), send_media_group=AsyncMock())
        context = SimpleNamespace(bot=bot)
        with override_settings(
            TELEGRAM_BOT_TOKEN="test-token",
            TELEGRAM_ADMIN_CHAT_ID="12345",
        ):
            async_to_sync(send_pending_requests)(context)

        bot.send_photo.assert_awaited_once()
        self.assertEqual(bot.send_photo.await_args.kwargs["reply_to_message_id"], 67890)
        change_request.refresh_from_db()
        self.assertTrue(change_request.telegram_photos_sent)

    def test_callback_is_acknowledged_before_applying_request(self):
        events = []
        query = SimpleNamespace(
            data="approve:42",
            answer=AsyncMock(side_effect=lambda *args, **kwargs: events.append("answered")),
            edit_message_reply_markup=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=query)

        def apply_request(*args, **kwargs):
            events.append("applied")

        with patch("places.telegram_bot.approve_change_request", side_effect=apply_request):
            async_to_sync(handle_callback)(update, SimpleNamespace())

        self.assertEqual(events, ["answered", "applied"])
        query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
        query.message.reply_text.assert_awaited_once_with(
            "Заявка #42 подтверждена и применена."
        )


class AuthTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_map_redirects_anonymous(self):
        response = self.client.get(reverse("places:map"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_login_with_correct_password(self):
        response = self.client.post(
            reverse("login"),
            {"password": settings.SITE_PASSWORD},
        )
        self.assertRedirects(response, reverse("places:home"))

    def test_login_with_wrong_password(self):
        response = self.client.post(reverse("login"), {"password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный пароль")
        self.assertNotIn("authenticated", self.client.session)

    def test_login_locks_after_limit(self):
        for _ in range(settings.LOGIN_ATTEMPT_LIMIT):
            self.client.post(reverse("login"), {"password": "wrong"})

        response = self.client.post(
            reverse("login"),
            {"password": settings.SITE_PASSWORD},
        )
        self.assertContains(response, "Слишком много попыток")

    def test_logout_flushes_session(self):
        session = self.client.session
        session["authenticated"] = True
        session.save()

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("authenticated", self.client.session)


class PhotoAccessTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.media_dir = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_dir)
        self.override.enable()
        self.place = create_place(title="Фото", latitude=10.0, longitude=10.0)
        self.photo = self._create_photo()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)
        super().tearDown()

    def _image_file(self, name, image_format, content_type):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "red").save(buffer, format=image_format)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)

    def _create_photo(self):
        return Photo.objects.create(
            place=self.place,
            image=self._image_file("original.jpg", "JPEG", "image/jpeg"),
            thumbnail=self._image_file("thumb.webp", "WEBP", "image/webp"),
            original_filename="original.jpg",
            mime_type="image/png",
            size_bytes=100,
        )

    def _photo_url(self, file_type):
        return reverse(
            "places:serve_photo",
            kwargs={"pk": self.photo.pk, "file_type": file_type},
        )

    def test_serves_original_with_jpeg_content_type(self):
        response = self.client.get(self._photo_url("original"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertTrue(b"".join(response.streaming_content))

    def test_serves_thumbnail_with_webp_content_type(self):
        response = self.client.get(self._photo_url("thumb"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertTrue(b"".join(response.streaming_content))

    def test_photo_requires_auth(self):
        response = Client().get(self._photo_url("original"))
        self.assertEqual(response.status_code, 302)

    def test_missing_file_returns_404(self):
        os.remove(self.photo.image.path)

        response = self.client.get(self._photo_url("original"))

        self.assertEqual(response.status_code, 404)

    def test_delete_photo_removes_record_and_files(self):
        photo_id = self.photo.pk
        image_path = self.photo.image.path
        thumbnail_path = self.photo.thumbnail.path

        with self.captureOnCommitCallbacks(execute=True):
            delete_photo(self.photo)

        self.assertFalse(Photo.objects.filter(pk=photo_id).exists())
        self.assertFalse(os.path.exists(image_path))
        self.assertFalse(os.path.exists(thumbnail_path))

    def test_delete_place_removes_photos_and_files(self):
        place_id = self.place.pk
        image_path = self.photo.image.path
        thumbnail_path = self.photo.thumbnail.path

        with self.captureOnCommitCallbacks(execute=True):
            delete_place(self.place)

        self.assertFalse(Place.objects.filter(pk=place_id).exists())
        self.assertFalse(Photo.objects.filter(place_id=place_id).exists())
        self.assertFalse(os.path.exists(image_path))
        self.assertFalse(os.path.exists(thumbnail_path))


class PhotoContentTypeTests(TestCase):
    def test_known_extensions(self):
        self.assertEqual(_photo_content_type("places/a/thumbnails/x.webp", "image/jpeg"), "image/webp")
        self.assertEqual(_photo_content_type("places/a/originals/x.jpg", "image/png"), "image/jpeg")
        self.assertEqual(_photo_content_type("places/a/originals/legacy.png", "image/jpeg"), "image/png")

    def test_unknown_extension_uses_stored_type(self):
        self.assertEqual(_photo_content_type("places/a/file.bin", "image/heic"), "image/heic")
        self.assertEqual(_photo_content_type("", ""), "application/octet-stream")
