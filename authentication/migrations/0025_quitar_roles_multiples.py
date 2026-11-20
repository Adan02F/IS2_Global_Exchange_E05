# -*- coding: utf-8 -*-
from django.db import migrations


class Migration(migrations.Migration):
    """Quita los roles adicionales: cada usuario tiene un único rol principal (``UserProfile.role``)."""

    dependencies = [
        ('authentication', '0024_eliminar_rol_corporate'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='userprofile',
            name='roles',
        ),
    ]
