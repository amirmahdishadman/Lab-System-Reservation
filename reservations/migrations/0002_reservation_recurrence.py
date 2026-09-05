from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reservations", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="reservation",
            name="series_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="reservation",
            name="recurrence",
            field=models.CharField(
                choices=[
                    ("none", "Does not repeat"),
                    ("daily", "Daily"),
                    ("weekly", "Weekly"),
                    ("monthly", "Monthly"),
                ],
                default="none",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="reservation",
            name="recurrence_until",
            field=models.DateField(blank=True, null=True),
        ),
    ]
