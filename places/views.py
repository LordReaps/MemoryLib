from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import PlaceForm
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
