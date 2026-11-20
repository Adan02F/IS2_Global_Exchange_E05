# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):
    """Marca las operaciones online cuya comisión + IVA se cobra dentro de la tasa aplicada."""

    dependencies = [
        ('procesamiento_operaciones', '0014_solicitud_anulacion_con_devolucion'),
    ]

    operations = [
        migrations.AddField(
            model_name='currencypurchasetransaction',
            name='comision_en_tasa',
            field=models.BooleanField(default=False, help_text='True si la comisión + IVA se cobró dentro de la tasa aplicada (operaciones online).', verbose_name='Comisión incluida en la tasa'),
        ),
        migrations.AddField(
            model_name='currencysaletransaction',
            name='comision_en_tasa',
            field=models.BooleanField(default=False, help_text='True si la comisión + IVA se cobró dentro de la tasa aplicada (operaciones online).', verbose_name='Comisión incluida en la tasa'),
        ),
    ]
