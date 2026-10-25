from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("places", "0006_changerequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="changerequest",
            name="telegram_photos_sent",
            field=models.BooleanField(default=False),
        ),
    ]
