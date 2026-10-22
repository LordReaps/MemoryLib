from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import auth
from .forms import PlaceForm, LoginForm
from .models import Place
from .services import create_place, delete_place, serialize_place, update_place


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
        },
    )


def _json_errors(form):
    return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)


@require_POST
def place_create(request):
    form = PlaceForm(request.POST)
    if not form.is_valid():
        return _json_errors(form)
    place = create_place(**form.cleaned_data)
    return JsonResponse({"ok": True, "place": serialize_place(place)})


@require_POST
def place_update(request, pk):
    place = get_object_or_404(Place, pk=pk)
    form = PlaceForm(request.POST, instance=place)
    if not form.is_valid():
        return _json_errors(form)
    place = update_place(place, **form.cleaned_data)
    return JsonResponse({"ok": True, "place": serialize_place(place)})


@require_POST
def place_delete(request, pk):
    place = get_object_or_404(Place, pk=pk)
    delete_place(place)
    return JsonResponse({"ok": True, "id": pk})


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