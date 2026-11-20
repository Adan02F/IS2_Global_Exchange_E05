# -*- coding: utf-8 -*-
"""
Actualiza las cotizaciones de las divisas a los nuevos valores por defecto (ver
``tasas_cambio/divisas_default.py``): compra y venta a ±4,5 % de la media de mercado
(USD media ₲ 7.150), para que la casa tenga margen incluso con el beneficio Corporativo (4 %).

Solo modifica las divisas que ya existen (no crea ninguna) y deja el cambio registrado en el
historial, igual que una actualización hecha desde el panel de cotizaciones.
"""
from decimal import Decimal

from django.db import migrations
from django.utils import timezone

# Valores fijos de esta migración (no se importan del módulo para que la migración no cambie si él cambia).
COTIZACIONES = {
    'USD': (Decimal('6830.0000'), Decimal('7470.0000')),
    'EUR': (Decimal('7665.0000'), Decimal('8385.0000')),
    'BRL': (Decimal('1335.0000'), Decimal('1465.0000')),
    'ARS': (Decimal('7.8500'), Decimal('8.6500')),
}


def actualizar_cotizaciones(apps, schema_editor):
    """Aplica las nuevas tasas de compra y venta y las registra en el historial."""
    ExchangeRate = apps.get_model('tasas_cambio', 'ExchangeRate')
    ExchangeRateHistory = apps.get_model('tasas_cambio', 'ExchangeRateHistory')
    ahora = timezone.now()
    for codigo, (compra, venta) in COTIZACIONES.items():
        tasa = ExchangeRate.objects.filter(currency_code=codigo).first()
        if tasa is None:
            continue
        tasa.buy_rate, tasa.sell_rate = compra, venta
        tasa.save(update_fields=['buy_rate', 'sell_rate', 'last_updated'])
        ExchangeRateHistory.objects.create(currency_code=codigo, buy_rate=compra, sell_rate=venta, timestamp=ahora)


class Migration(migrations.Migration):

    dependencies = [
        ('tasas_cambio', '0013_notificacion_facturacion'),
    ]

    operations = [
        migrations.RunPython(actualizar_cotizaciones, migrations.RunPython.noop),
    ]
