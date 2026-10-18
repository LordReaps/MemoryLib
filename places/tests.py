from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Place
from .services import create_place, delete_place, update_place


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


class MapViewTests(TestCase):
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


class PlaceViewTests(TestCase):
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
