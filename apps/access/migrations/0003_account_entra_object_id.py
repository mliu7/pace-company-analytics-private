from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("access", "0002_permissions_page")]

    operations = [
        migrations.AddField(
            model_name="account",
            name="entra_object_id",
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
    ]
