from django.contrib import admin

from .models import Folder, Photo, Place


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)

@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("title", "folder", "latitude", "longitude", "start_date", "end_date", "updated_at")
    search_fields = ("title", "description")
    list_filter = ("folder", "start_date", "end_date")

@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('id', 'place', 'original_filename', 'size_bytes', 'created_at')
    list_filter = ('place',)