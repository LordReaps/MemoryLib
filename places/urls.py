from django.urls import path

from . import views

app_name = "places"

urlpatterns = [
    path("", views.map_view, name="map"),
    path("places/create/", views.place_create, name="place_create"),
    path("places/<int:pk>/update/", views.place_update, name="place_update"),
    path("places/<int:pk>/delete/", views.place_delete, name="place_delete"),
    path("photos/<int:pk>/delete/", views.photo_delete, name="photo_delete"),
    path("photos/<int:pk>/<str:file_type>/", views.serve_photo, name="serve_photo"),
]
