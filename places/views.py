from django.http import JsonResponse, Http404, FileResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import auth
from .forms import PlaceForm, LoginForm
from .models import ChangeRequest, Place, Photo
from .services import (
    ServiceOperationError,
    add_photos_to_place,
    approve_change_request,
    create_place,
    delete_photo,
    delete_place,
    reject_change_request,
    serialize_place,
    update_place,
)


def _place_url_template(name):
    return reverse(f"places:{name}", kwargs={"pk": 0}).replace("/0/", "/{pk}/")


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


def _json_errors(form):
    return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)


def _json_service_error(exc, status=400):
    return JsonResponse({"ok": False, "error": str(exc)}, status=status)


@require_POST
def place_create(request):
    form = PlaceForm(request.POST)
    if not form.is_valid():
        return _json_errors(form)

    try:
        place = create_place(**form.cleaned_data)
    except ServiceOperationError as exc:
        return _json_service_error(exc, 500)

    files = request.FILES.getlist("photos")
    if files:
        try:
            add_photos_to_place(place.id, files)
        except ServiceOperationError as exc:
            return _json_service_error(exc, 500)

    return JsonResponse({"ok": True, "place": serialize_place(place)})


@require_POST
def place_update(request, pk):
    place = get_object_or_404(Place, pk=pk)
    form = PlaceForm(request.POST, instance=place)
    if not form.is_valid():
        return _json_errors(form)

    try:
        place = update_place(place, **form.cleaned_data)
    except ServiceOperationError as exc:
        return _json_service_error(exc, 500)

    files = request.FILES.getlist("photos")
    if files:
        try:
            add_photos_to_place(place.id, files)
        except ServiceOperationError as exc:
            return _json_service_error(exc, 500)

    return JsonResponse({"ok": True, "place": serialize_place(place)})


@require_POST
def place_delete(request, pk):
    place = get_object_or_404(Place, pk=pk)
    try:
        delete_place(place)
    except ServiceOperationError as exc:
        return _json_service_error(exc, 500)
    return JsonResponse({"ok": True, "id": pk})


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
    place_id = photo.place.id
    try:
        delete_photo(photo)
    except ServiceOperationError as exc:
        return _json_service_error(exc, 500)
    return JsonResponse({"ok": True, "place_id": place_id})


@require_POST
def change_request_approve(request, pk):
    change_request = get_object_or_404(ChangeRequest, pk=pk)
    try:
        approve_change_request(change_request.pk, reviewed_by="admin")
    except ServiceOperationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "status": "approved", "id": pk})


@require_POST
def change_request_reject(request, pk):
    change_request = get_object_or_404(ChangeRequest, pk=pk)
    try:
        reject_change_request(change_request.pk, reviewed_by="admin")
    except ServiceOperationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "status": "rejected", "id": pk})


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
