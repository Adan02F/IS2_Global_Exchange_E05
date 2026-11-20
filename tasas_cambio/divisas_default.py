# -*- coding: utf-8 -*-
"""
Cotizaciones por defecto de las divisas que opera la casa de cambios (única fuente).

Las usan el comando ``seed_data``, las vistas que crean las divisas si faltan y el historial simulado.

Criterio del spread: el beneficio por categoría se aplica sobre la tasa (compra × (1 + b),
venta × (1 − b)). Para que la casa siga ganando con el beneficio más alto (Corporativo 4 %),
la tasa con beneficio tiene que quedar del lado favorable de la tasa media de mercado:

    venta × (1 − 0,04) > media    y    compra × (1 + 0,04) < media

Por eso compra y venta están a ±4,5 % de la media. Ejemplo USD (media ₲ 7.150):

    Minorista    compra 6.830   venta 7.470   margen 320 / 320 por USD
    VIP 2 %      compra 6.967   venta 7.321   margen 183 / 171 por USD
    Corporativo  compra 7.103   venta 7.171   margen  47 /  21 por USD

A ese margen se suma la comisión (0,5 % menos IVA).
"""
from decimal import Decimal

# (código, nombre, símbolo, compra, venta) en Guaraníes por unidad.
DIVISAS_DEFAULT = [
    ('USD', 'Dólar Estadounidense', '$', Decimal('6830.0000'), Decimal('7470.0000')),  # media 7.150
    ('EUR', 'Euro', '€', Decimal('7665.0000'), Decimal('8385.0000')),                  # media 8.025
    ('BRL', 'Real Brasileño', 'R$', Decimal('1335.0000'), Decimal('1465.0000')),       # media 1.400
    ('ARS', 'Peso Argentino', '$', Decimal('7.8500'), Decimal('8.6500')),               # media 8,25
    ('PYG', 'Guaraní Paraguayo', '₲', Decimal('1.0000'), Decimal('1.0000')),
]

# Mismos datos sin símbolo: (código, nombre, compra, venta).
DIVISAS_DEFAULT_SIN_SIMBOLO = [(c, n, compra, venta) for c, n, _, compra, venta in DIVISAS_DEFAULT]

# Tasa de compra por defecto de cada divisa, para cálculos de respaldo.
TASA_COMPRA_DEFAULT = {c: compra for c, _, _, compra, _ in DIVISAS_DEFAULT}
