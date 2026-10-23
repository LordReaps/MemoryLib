import os
import shutil
import tempfile
from io import BytesIO

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Photo, Place
from .services import create_place, delete_photo, delete_place, update_place


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
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "places-data")
        self.assertContains(response, '"latitude": 52.52')
        self.assertContains(response, '"longitude": 13.405')
        self.assertContains(response, "add-place-btn")
        self.assertContains(response, 'id="place-sheet"')
        self.assertContains(response, 'id="lightbox-prev"')
        self.assertContains(response, 'id="photo-preview"')


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
        self.assertRedirects(response, reverse("places:map"))

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
