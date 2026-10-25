from django.conf import settings
from django.http import JsonResponse, Http404, FileResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import auth
from .forms import PlaceForm, LoginForm
from .models import Photo, Place
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


def _place_url_template(name):
    return reverse(f"places:{name}", kwargs={"pk": 0}).replace("/0/", "/{pk}/")


def _telegram_enabled():
    # return bool(getattr(settings, "TELEGRAM_BOT_TOKEN", "").strip()) and bool(
    #    str(getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "")).strip())
    return False


def _json_errors(form):
    return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)


def map_view(request):
    places_data = [serialize_place(place) for place in Place.objects.all()]
    return render(
        request,
        "map.html",
        {
            "places_data": places_data,
            "create_url": reverse("places:place_create"),
            "update_url_template": _place_url_template("place_update"),
            "delete_url_template": _place_url_template("place_delete"),
            "delete_photo_url_template": reverse("places:photo_delete", kwargs={"pk": 0}).replace("/0/", "/{pk}/"),
        },
    )


@require_POST
def place_create(request):
    form = PlaceForm(request.POST)
    if not form.is_valid():
        return _json_errors(form)

    files = request.FILES.getlist("photos")

    if _telegram_enabled():
        queue = queue_place_create(
            title=form.cleaned_data["title"],
            description=form.cleaned_data.get("description") or "",
            latitude=form.cleaned_data["latitude"],
            longitude=form.cleaned_data["longitude"],
            start_date=form.cleaned_data.get("start_date"),
            end_date=form.cleaned_data.get("end_date"),
            files=files,
        )
        return JsonResponse(
            {
                "ok": True,
                "status": "pending",
                "change_request_id": queue.pk,
                "message": "Изменение отправлено на подтверждение в Telegram.",
            }
        )

    place = create_place(
        title=form.cleaned_data["title"],
        description=form.cleaned_data.get("description") or "",
        latitude=form.cleaned_data["latitude"],
        longitude=form.cleaned_data["longitude"],
        start_date=form.cleaned_data.get("start_date"),
        end_date=form.cleaned_data.get("end_date"),
        files=files,
    )
    return JsonResponse({"ok": True, "place": serialize_place(place)})


@require_POST
def place_update(request, pk):
    place = get_object_or_404(Place, pk=pk)
    form = PlaceForm(request.POST, instance=place)
    if not form.is_valid():
        return _json_errors(form)

    files = request.FILES.getlist("photos")

    if _telegram_enabled():
        queue = queue_place_update(
            place,
            title=form.cleaned_data["title"],
            description=form.cleaned_data.get("description") or "",
            latitude=form.cleaned_data["latitude"],
            longitude=form.cleaned_data["longitude"],
            start_date=form.cleaned_data.get("start_date"),
            end_date=form.cleaned_data.get("end_date"),
            files=files,
        )
        return JsonResponse(
            {
                "ok": True,
                "status": "pending",
                "change_request_id": queue.pk,
                "message": "Изменение отправлено на подтверждение в Telegram.",
            }
        )

    place = update_place(
        place,
        title=form.cleaned_data["title"],
        description=form.cleaned_data.get("description") or "",
        latitude=form.cleaned_data["latitude"],
        longitude=form.cleaned_data["longitude"],
        start_date=form.cleaned_data.get("start_date"),
        end_date=form.cleaned_data.get("end_date"),
        files=files,
    )
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
                "message": "Удаление отправлено на подтверждение в Telegram.",
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

    return FileResponse(file_field.open("rb"), content_type=photo.mime_type or "application/octet-stream")


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
                "message": "Удаление фотографии отправлено на подтверждение в Telegram.",
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
        return redirect("places:map")
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
                return redirect("places:map")
            else:
                auth.register_failure(ip)
                form.add_error(None, "Неверный пароль.")

    return render(request, "login.html", {"form": form})


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("login")