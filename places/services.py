from django.db import transaction

from .models import Place


def serialize_place(place):
    return {
        "id": place.id,
        "title": place.title,
        "description": place.description,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "start_date": place.start_date.isoformat() if place.start_date else None,
        "end_date": place.end_date.isoformat() if place.end_date else None,
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
    place.delete()
