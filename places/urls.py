from django.urls import path

from . import views

app_name = "places"

urlpatterns = [
    path("", views.map_view, name="map"),
    path("places/create/", views.place_create, name="place_create"),
    path("places/<int:pk>/update/", views.place_update, name="place_update"),
    path("places/<int:pk>/delete/", views.place_delete, name="place_delete"),
]
