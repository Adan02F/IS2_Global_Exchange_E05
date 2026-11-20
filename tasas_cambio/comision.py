# -*- coding: utf-8 -*-
"""
Comisión de las operaciones online, cobrada dentro de la tasa.

El cliente no ve una línea de comisión: la tasa que se le muestra (simulador, compra, venta) ya incluye la
comisión del 0,5 % más su IVA (10 % de la comisión), es decir un cargo total del 0,55 % sobre el monto.
El cliente recibe ``monto convertido × (1 − 0,0055)``:

* Compra PYG→divisa:   tasa mostrada = tasa con beneficio / 0,9945 (paga más guaraníes por unidad).
* Venta divisa→PYG:    tasa mostrada = tasa con beneficio × 0,9945 (recibe menos guaraníes por unidad).
* Cruzada divisa→divisa: tasa cruzada mostrada = (compra origen / venta destino) × 0,9945.

La transacción guarda igual ``commission_amount`` y ``tax_amount`` (calculados sobre el monto sin cargo) para
la contabilidad y la facturación, y ``comision_en_tasa = True`` para que el reporte de ganancias no la
cuente dos veces (una como comisión y otra dentro del margen cambiario).
"""
from decimal import Decimal

COMISION = Decimal('0.005')
IVA_COMISION = Decimal('0.10')
CARGO_EN_TASA = COMISION * (Decimal('1') + IVA_COMISION)   # 0,0055
FACTOR_NETO = Decimal('1') - CARGO_EN_TASA                  # 0,9945
