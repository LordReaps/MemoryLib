from django.contrib import admin

from .models import Place, Photo


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("title", "latitude", "longitude", "start_date", "end_date", "updated_at")
    search_fields = ("title", "description")
    list_filter = ("start_date", "end_date")

@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('id', 'place', 'original_filename', 'size_bytes', 'created_at')
    list_filter = ('place',)