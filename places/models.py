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
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": "Дата окончания не может быть раньше даты начала."}
            )


class Photo(models.Model):
    place = models.ForeignKey(
        Place,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Место",
    )
    image = models.ImageField(
        upload_to="places/%Y/%m/%d/originals/",
        verbose_name="Оригинал",
    )
    thumbnail = models.ImageField(
        upload_to="places/%Y/%m/%d/thumbnails/",
        verbose_name="Миниатюра",
        blank=True,
    )
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


class ChangeRequest(models.Model):
    ACTION_CREATE = "CREATE"
    ACTION_UPDATE = "UPDATE"
    ACTION_DELETE = "DELETE"

    ENTITY_PLACE = "place"
    ENTITY_PHOTO = "photo"

    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_FAILED = "FAILED"

    ACTION_CHOICES = [
        (ACTION_CREATE, "Создание"),
        (ACTION_UPDATE, "Изменение"),
        (ACTION_DELETE, "Удаление"),
    ]

    ENTITY_CHOICES = [
        (ENTITY_PLACE, "Место"),
        (ENTITY_PHOTO, "Фотография"),
    ]

    STATUS_CHOICES = [
        (STATUS_PENDING, "Ожидает подтверждения"),
        (STATUS_APPROVED, "Подтверждена"),
        (STATUS_REJECTED, "Отклонена"),
        (STATUS_FAILED, "Ошибка"),
    ]

    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        db_index=True,
    )
    entity_type = models.CharField(
        max_length=20,
        choices=ENTITY_CHOICES,
        default=ENTITY_PLACE,
        db_index=True,
    )
    entity_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        db_index=True,
    )
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    temp_storage_path = models.CharField(max_length=500, blank=True, default="")
    request_hash = models.CharField(max_length=128, blank=True, default="", db_index=True)
    error_message = models.TextField(blank=True, default="")
    telegram_chat_id = models.CharField(max_length=50, blank=True, default="")
    telegram_message_id = models.CharField(max_length=50, blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заявка на изменение"
        verbose_name_plural = "Заявки на изменение"

    def __str__(self):
        return f"ChangeRequest #{self.pk}: {self.get_action_display()} / {self.get_status_display()}"