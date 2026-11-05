import logging
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse, Http404, FileResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import auth
from .forms import PlaceForm, LoginForm
from .models import Folder, Photo, Place
from .services import (
    ServiceOperationError,
    approve_change_request,
    create_place,
    delete_photo,
    delete_place,
    queue_photo_delete,
    queue_place_create,
    queue_place_delete,
    queue_place_update,
    reject_change_request,
    serialize_place,
    update_place,
)

logger = logging.getLogger(__name__)


def _place_url_template(name):
    return reverse(f"places:{name}", kwargs={"pk": 0}).replace("/0/", "/{pk}/")


def _telegram_configuration():
    return (
        bool(getattr(settings, "ENABLE_TG_CONFIRMATION", False)),
        bool(str(getattr(settings, "TELEGRAM_BOT_TOKEN", "")).strip()),
        bool(str(getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "")).strip()),
    )


def _telegram_enabled():
    return all(_telegram_configuration())


def _json_errors(form):
    return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)


def _save_failed(message, status=400):
    return JsonResponse({"ok": False, "errors": {"__all__": [message]}}, status=status)


RECENT_LIMIT = 10


def _folder_cards():
    """Папки с количеством мест и обложкой — первым фото первой записи."""
    folders = (
        Folder.objects.annotate(place_count=Count("places"))
        .order_by("name")
        .prefetch_related(
            Prefetch(
                "places",
                queryset=Place.objects.order_by("created_at").prefetch_related("photos"),
            )
        )
    )

    cards = []
    for folder in folders:
        first_place = next(iter(folder.places.all()), None)
        photo = first_place.photos.all().first() if first_place else None
        cards.append({"folder": folder, "photo": photo})
    return cards


def home_view(request):
    places = Place.objects.select_related("folder").prefetch_related("photos")
    folder_cards = _folder_cards()
    return render(
        request,
        "home.html",
        {
            "recent_places": places.order_by("-created_at")[:RECENT_LIMIT],
            "folder_cards": folder_cards,
            "places_count": Place.objects.count(),
            "folders_count": len(folder_cards),
            "photos_count": Photo.objects.count(),
            "create_folder_url": reverse("places:folder_create"),
            "folder_url_template": _place_url_template("folder_detail"),
        },
    )


def folder_list(request):
    return render(request, "folders.html", {"folder_cards": _folder_cards()})


def map_view(request):
    places_data = [
        serialize_place(place) for place in Place.objects.select_related("folder")
    ]
    return render(
        request,
        "map.html",
        {
            "places_data": places_data,
            "folders": Folder.objects.all(),
            "create_url": reverse("places:place_create"),
            "create_folder_url": reverse("places:folder_create"),
            "detail_url_template": _place_url_template("place_detail"),
            "update_url_template": _place_url_template("place_update"),
            "delete_url_template": _place_url_template("place_delete"),
            "delete_photo_url_template": reverse("places:photo_delete", kwargs={"pk": 0}).replace("/0/", "/{pk}/"),
        },
    )


SIMILAR_LIMIT = 4
SIMILAR_DAYS = 14


def _similar_places(place, limit=SIMILAR_LIMIT, days=SIMILAR_DAYS):
    others = (
        Place.objects.select_related("folder")
        .prefetch_related("photos")
        .exclude(pk=place.pk)
    )
    result = []

    if place.folder_id:
        result.extend(others.filter(folder_id=place.folder_id).order_by("title")[:limit])

    if len(result) >= limit:
        return result

    reference = place.start_date or place.end_date
    if not reference:
        return result

    window_start = reference - timedelta(days=days)
    window_end = reference + timedelta(days=days)
    candidates = others.filter(
        Q(start_date__range=(window_start, window_end))
        | Q(end_date__range=(window_start, window_end))
    ).exclude(pk__in=[similar.pk for similar in result])

    def distance(candidate):
        candidate_date = candidate.start_date or candidate.end_date
        return abs((candidate_date - reference).days)

    result.extend(sorted(candidates, key=distance)[: limit - len(result)])
    return result


def place_detail(request, pk):
    place = get_object_or_404(
        Place.objects.select_related("folder").prefetch_related("photos"), pk=pk
    )
    return render(
        request,
        "place.html",
        {"place": place, "similar_places": _similar_places(place)},
    )


def folder_detail(request, pk):
    folder = get_object_or_404(Folder, pk=pk)
    places = folder.places.select_related("folder").prefetch_related("photos")
    return render(
        request,
        "folder.html",
        {"folder": folder, "places": places.order_by("title")},
    )


@require_POST
def folder_delete(request, pk):
    folder = get_object_or_404(Folder, pk=pk)
    folder.delete()
    return redirect("places:home")


@require_POST
def folder_create(request):
    name = (request.POST.get("name") or "").strip()
    if not name or len(name) > 120:
        return JsonResponse(
            {"ok": False, "errors": {"name": ["Введите название папки (до 120 символов)."]}},
            status=400,
        )
    if Folder.objects.filter(name__iexact=name).exists():
        return JsonResponse(
            {"ok": False, "errors": {"name": ["Папка с таким названием уже есть."]}},
            status=400,
        )

    folder = Folder.objects.create(name=name)
    return JsonResponse({"ok": True, "folder": {"id": folder.pk, "name": folder.name}})


@require_POST
def place_create(request):
    form = PlaceForm(request.POST)
    if not form.is_valid():
        logger.warning("Place create rejected: invalid form: %s", form.errors.get_json_data())
        return _json_errors(form)

    files = request.FILES.getlist("photos")
    telegram_configuration = _telegram_configuration()
    telegram_enabled = all(telegram_configuration)
    logger.info(
        "Place create requested: telegram_enabled=%s, confirmation_enabled=%s, "
        "token_configured=%s, admin_chat_configured=%s, photos=%d",
        telegram_enabled,
        *telegram_configuration,
        len(files),
    )

    if telegram_enabled:
        try:
            queue = queue_place_create(
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description") or "",
                folder=form.cleaned_data.get("folder"),
                latitude=form.cleaned_data["latitude"],
                longitude=form.cleaned_data["longitude"],
                start_date=form.cleaned_data.get("start_date"),
                end_date=form.cleaned_data.get("end_date"),
                files=files,
            )
        except Exception:
            logger.exception("Place create failed while saving queued files")
            return _save_failed(
                "Не удалось сохранить фотографии. Попробуйте ещё раз.", status=500
            )

        logger.info("Place create queued for review: change_request_id=%s", queue.pk)
        return JsonResponse(
            {
                "ok": True,
                "status": "pending",
                "change_request_id": queue.pk,
                "message": "Изменение отправлено на рассмотрение.",
            }
        )

    try:
        place = create_place(
            title=form.cleaned_data["title"],
            description=form.cleaned_data.get("description") or "",
            folder=form.cleaned_data.get("folder"),
            latitude=form.cleaned_data["latitude"],
            longitude=form.cleaned_data["longitude"],
            start_date=form.cleaned_data.get("start_date"),
            end_date=form.cleaned_data.get("end_date"),
            files=files,
        )
    except Exception:
        logger.exception("Place create failed")
        return _save_failed("Не удалось сохранить место. Попробуйте ещё раз.")

    logger.info("Place created immediately: place_id=%s", place.pk)
    return JsonResponse({"ok": True, "place": serialize_place(place)})


@require_POST
def place_update(request, pk):
    place = get_object_or_404(Place, pk=pk)
    form = PlaceForm(request.POST, instance=place)
    if not form.is_valid():
        logger.warning(
            "Place update rejected: place_id=%s, invalid form: %s",
            pk,
            form.errors.get_json_data(),
        )
        return _json_errors(form)

    files = request.FILES.getlist("photos")
    telegram_configuration = _telegram_configuration()
    telegram_enabled = all(telegram_configuration)
    logger.info(
        "Place update requested: place_id=%s, telegram_enabled=%s, confirmation_enabled=%s, "
        "token_configured=%s, admin_chat_configured=%s, photos=%d",
        pk,
        telegram_enabled,
        *telegram_configuration,
        len(files),
    )

    if telegram_enabled:
        try:
            queue = queue_place_update(
                place,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description") or "",
                folder=form.cleaned_data.get("folder"),
                latitude=form.cleaned_data["latitude"],
                longitude=form.cleaned_data["longitude"],
                start_date=form.cleaned_data.get("start_date"),
                end_date=form.cleaned_data.get("end_date"),
                files=files,
            )
        except Exception:
            logger.exception("Place update failed while saving queued files")
            return _save_failed(
                "Не удалось сохранить фотографии. Попробуйте ещё раз.", status=500
            )

        logger.info(
            "Place update queued for review: place_id=%s, change_request_id=%s",
            pk,
            queue.pk,
        )
        return JsonResponse(
            {
                "ok": True,
                "status": "pending",
                "change_request_id": queue.pk,
                "message": "Изменение отправлено на рассмотрение.",
            }
        )

    try:
        place = update_place(
            place,
            title=form.cleaned_data["title"],
            description=form.cleaned_data.get("description") or "",
            folder=form.cleaned_data.get("folder"),
            latitude=form.cleaned_data["latitude"],
            longitude=form.cleaned_data["longitude"],
            start_date=form.cleaned_data.get("start_date"),
            end_date=form.cleaned_data.get("end_date"),
            files=files,
        )
    except Exception:
        logger.exception("Place update failed")
        return _save_failed("Не удалось сохранить место. Попробуйте ещё раз.")

    logger.info("Place updated immediately: place_id=%s", place.pk)
    return JsonResponse({"ok": True, "place": serialize_place(place)})


@require_POST
def place_delete(request, pk):
    place = get_object_or_404(Place, pk=pk)

    if _telegram_enabled():
        queue = queue_place_delete(place)
        return JsonResponse(
            {
                "ok": True,
                "status": "pending",
                "change_request_id": queue.pk,
                "message": "Удаление отправлено на рассмотрение.",
            }
        )

    delete_place(place)
    return JsonResponse({"ok": True, "id": pk})


@require_POST
def change_request_approve(request, pk):
    try:
        change_request = approve_change_request(pk, reviewed_by="web")
    except ServiceOperationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "status": change_request.status,
            "change_request_id": pk,
        }
    )


@require_POST
def change_request_reject(request, pk):
    try:
        change_request = reject_change_request(pk, reviewed_by="web")
    except ServiceOperationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "status": change_request.status,
            "change_request_id": pk,
        }
    )


def _photo_content_type(name, stored_mime_type):
    """Тип отдаваемого файла по расширению: оригинал у нас всегда .jpg, миниатюра — .webp."""
    suffix = Path(name or "").suffix.lower()
    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    return content_types.get(suffix) or stored_mime_type or "application/octet-stream"


def serve_photo(request, pk, file_type):
    if not request.session.get("authenticated"):
        raise Http404

    try:
        photo = Photo.objects.get(pk=pk)
    except Photo.DoesNotExist:
        raise Http404

    if file_type == "original":
        file_field = photo.image
    elif file_type == "thumb":
        file_field = photo.thumbnail
    else:
        raise Http404

    if not file_field:
        raise Http404

    return FileResponse(
        file_field.open("rb"),
        content_type=_photo_content_type(file_field.name, photo.mime_type),
    )


@require_POST
def photo_delete(request, pk):
    photo = get_object_or_404(Photo, pk=pk)

    if _telegram_enabled():
        queue = queue_photo_delete(photo)
        return JsonResponse(
            {
                "ok": True,
                "status": "pending",
                "change_request_id": queue.pk,
                "message": "Удаление фотографии отправлено на рассмотрение.",
            }
        )

    try:
        delete_photo(photo)
    except Exception:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Не удалось удалить фотографию. Попробуйте ещё раз."]}},
            status=400,
        )

    return JsonResponse({"ok": True, "place_id": photo.place_id})


def login_view(request):
    if request.session.get("authenticated"):
        return redirect("places:home")
    form = LoginForm(request.POST or None)
    ip = request.META.get("REMOTE_ADDR", "")

    if request.method == "POST":
        if auth.is_locked(ip):
            form.add_error(None, "Слишком много попыток. Попробуй позже.")
        elif form.is_valid():
            if auth.check_password(form.cleaned_data["password"]):
                auth.reset(ip)
                request.session.cycle_key()
                request.session["authenticated"] = True
                next_url = request.GET.get("next") or request.POST.get("next")
                if next_url and url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)
                return redirect("places:home")
            else:
                auth.register_failure(ip)
                form.add_error(None, "Неверный пароль.")

    return render(request, "login.html", {"form": form})


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("login")