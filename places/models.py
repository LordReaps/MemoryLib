from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Place(models.Model):
    title = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    latitude = models.FloatField(
        "Широта",
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.FloatField(
        "Долгота",
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    start_date = models.DateField("Дата начала", null=True, blank=True)
    end_date = models.DateField("Дата окончания", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Изменено", auto_now=True)

    class Meta:
        ordering = ["title"]
        verbose_name = "Место"
        verbose_name_plural = "Места"

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if (
            self.start_date
            and self.end_date
            and self.end_date < self.start_date
        ):
            raise ValidationError(
                {"end_date": "Дата окончания не может быть раньше даты начала."}
            )

class Photo(models.Model):
    place = models.ForeignKey(
        Place,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Место"
    )
    image = models.ImageField(upload_to="places/%Y/%m/%d/originals/", verbose_name="Оригинал")
    thumbnail = models.ImageField(upload_to="places/%Y/%m/%d/thumbnails/", verbose_name="Миниатюра", blank=True)
    original_filename = models.CharField(max_length=255, verbose_name="Имя файла")
    caption = models.CharField(max_length=255, blank=True, verbose_name="Подпись")
    mime_type = models.CharField(max_length=100, blank=True, verbose_name="MIME-тип")
    size_bytes = models.PositiveBigIntegerField(default=0, verbose_name="Размер (байт)")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Загружено")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Фотография"
        verbose_name_plural = "Фотографии"

    def __str__(self):
        return self.original_filename