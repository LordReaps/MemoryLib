from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.db import migrations, transaction


def _empty_file_data(name):
    return SimpleUploadedFile(name=name, content=b"placeholder", size=9)


class Migration(migrations.Migration):
    initial = False

    dependencies = [
        ("places", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChangeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("CREATE", "Создание"), ("UPDATE", "Изменение"), ("DELETE", "Удаление")], db_index=True, max_length=20)),
                ("entity_type", models.CharField(choices=[("place", "Место"), ("photo", "Фотография")], db_index=True, default="place", max_length=20)),
                ("entity_id", models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("PENDING", "Ожидает подтверждения"), ("APPROVED", "Подтверждена"), ("REJECTED", "Отклонена"), ("APPLIED", "Применена"), ("FAILED", "Ошибка"), ("CANCELLED", "Отменена")], db_index=True, default="PENDING", max_length=20)),
                ("temp_storage_path", models.CharField(blank=True, default="", max_length=500)),
                ("request_hash", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("error_message", models.TextField(blank=True, default="")),
                ("telegram_chat_id", models.CharField(blank=True, default="", max_length=50)),
                ("telegram_message_id", models.CharField(blank=True, default="", max_length=50)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_by", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Заявка на изменение",
                "verbose_name_plural": "Заявки на изменение",
                "ordering": ["-created_at"],
            },
        ),
    ]
