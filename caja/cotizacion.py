# -*- coding: utf-8 -*-
"""
Cotización de operaciones en efectivo, compartida por la ventanilla (PSE-38) y la terminal de autoservicio.

Las dos vías de efectivo usan exactamente el mismo cálculo: tasa vigente ajustada por el beneficio de la
categoría del cliente y comisión del 0,5 %.
"""
from dataclasses import dataclass
from decimal import Decimal

from tasas_cambio.divisas_default import TASA_COMPRA_DEFAULT
from tasas_cambio.models import ClientBenefitRule, ExchangeRate
from tasas_cambio.views import ensure_default_benefit_rules

COMISION_EFECTIVO = Decimal('0.005')
TASAS_POR_DEFECTO = TASA_COMPRA_DEFAULT


def beneficio_de_cliente(cliente):
    """
    Porcentaje de beneficio y nombre de la categoría del cliente (Minorista, VIP, Corporativo).

    Args:
        cliente (Cliente | None): Cliente de la operación.

    Returns:
        tuple[Decimal, str]: Porcentaje de beneficio y nombre de la categoría.
    """
    if not cliente:
        return Decimal('0.00'), 'Minorista'
    ensure_default_benefit_rules()
    categoria = cliente.categoria or 'MINORISTA'
    regla = ClientBenefitRule.objects.filter(category_code__iexact=categoria).first()
    if regla:
        return regla.benefit_percentage, regla.category_name
    if str(categoria).upper() == 'VIP':
        return Decimal('2.00'), 'VIP'
    if str(categoria).upper() == 'CORPORATIVO':
        return Decimal('4.00'), 'Corporativo'
    return Decimal('0.00'), 'Minorista'


def tasa_estandar(moneda, tipo_operacion):
    """
    Tasa vigente sin beneficio: de compra si la casa compra, de venta si la casa vende.

    Args:
        moneda (str): Código de la divisa.
        tipo_operacion (str): ``COMPRA`` o ``VENTA``.

    Returns:
        Decimal: Tasa en Guaraníes por unidad (1 para PYG).
    """
    if moneda == 'PYG':
        return Decimal('1.0000')
    tasa = ExchangeRate.objects.filter(currency_code=moneda).first()
    if not tasa:
        return TASAS_POR_DEFECTO.get(moneda, TASA_COMPRA_DEFAULT['USD'])
    return tasa.buy_rate if tipo_operacion == 'COMPRA' else tasa.sell_rate


def tasa_con_beneficio(moneda, tipo_operacion, benefit_percentage):
    """
    Tasa vigente ajustada por el beneficio de la categoría del cliente.

    Args:
        moneda (str): Código de la divisa.
        tipo_operacion (str): ``COMPRA`` o ``VENTA``.
        benefit_percentage (Decimal): Beneficio de la categoría (%).

    Returns:
        Decimal: Tasa aplicada.
    """
    base = tasa_estandar(moneda, tipo_operacion)
    if moneda == 'PYG' or benefit_percentage <= 0:
        return base
    factor = benefit_percentage / Decimal('100.00')
    if tipo_operacion == 'COMPRA':
        return (base * (Decimal('1.0000') + factor)).quantize(Decimal('0.0001'))
    return (base * (Decimal('1.0000') - factor)).quantize(Decimal('0.0001'))


@dataclass
class CotizacionEfectivo:
    """
    Resultado de cotizar una operación en efectivo.

    Attributes:
        monto_origen (Decimal): Monto que entrega el cliente.
        monto_destino (Decimal): Monto que recibe el cliente.
        tasa (Decimal): Tasa aplicada (de la divisa extranjera).
        tasa_std (Decimal): Tasa estándar, sin beneficio.
        comision (Decimal): Comisión en la moneda de origen.
        comision_pyg (Decimal): Comisión en Guaraníes.
        valor_pyg (Decimal): Valor de la operación en Guaraníes.
        total_a_cobrar_entregar (Decimal): Monto en efectivo que mueve la caja.
    """
    monto_origen: Decimal
    monto_destino: Decimal
    tasa: Decimal
    tasa_std: Decimal
    comision: Decimal
    comision_pyg: Decimal
    valor_pyg: Decimal
    total_a_cobrar_entregar: Decimal


def cotizar_efectivo(tipo_operacion, moneda_origen, moneda_destino, monto, benefit_percentage):
    """
    Cotiza una operación en efectivo.

    * ``COMPRA`` (la casa compra divisas): ``monto`` es lo que entrega el cliente en la moneda de origen.
    * ``VENTA`` (la casa vende divisas): ``monto`` es lo que recibe el cliente en la moneda de destino.

    Args:
        tipo_operacion (str): ``COMPRA`` o ``VENTA``.
        moneda_origen (str): Moneda que entrega el cliente.
        moneda_destino (str): Moneda que recibe el cliente.
        monto (Decimal): Monto ingresado.
        benefit_percentage (Decimal): Beneficio por categoría del cliente (%).

    Returns:
        CotizacionEfectivo: Montos, tasas y comisión de la operación.
    """
    tasa_origen = tasa_con_beneficio(moneda_origen, tipo_operacion, benefit_percentage)
    tasa_destino = tasa_con_beneficio(moneda_destino, tipo_operacion, benefit_percentage)
    tasa = tasa_origen if moneda_origen != 'PYG' else tasa_destino
    tasa_std = tasa_estandar(moneda_origen if moneda_origen != 'PYG' else moneda_destino, tipo_operacion)

    if tipo_operacion == 'COMPRA':
        monto_origen = monto
        valor_pyg = monto_origen if moneda_origen == 'PYG' else (monto_origen * tasa_origen)
        comision = monto_origen * COMISION_EFECTIVO
        comision_pyg = comision if moneda_origen == 'PYG' else (comision * tasa_origen)
        neto_pyg = valor_pyg - comision_pyg
        monto_destino = neto_pyg if moneda_destino == 'PYG' else (neto_pyg / tasa_destino if tasa_destino > 0 else neto_pyg)
        total = monto_destino
    else:
        monto_destino = monto
        neto_pyg = monto_destino if moneda_destino == 'PYG' else (monto_destino * tasa_destino)
        valor_pyg = neto_pyg / (Decimal('1') + COMISION_EFECTIVO)
        comision_pyg = neto_pyg - valor_pyg
        comision = comision_pyg if moneda_origen == 'PYG' else (comision_pyg / tasa_origen if tasa_origen > 0 else comision_pyg)
        monto_origen = valor_pyg if moneda_origen == 'PYG' else (valor_pyg / tasa_origen if tasa_origen > 0 else valor_pyg)
        total = monto_origen

    return CotizacionEfectivo(
        monto_origen=monto_origen, monto_destino=monto_destino, tasa=tasa, tasa_std=tasa_std,
        comision=comision, comision_pyg=comision_pyg, valor_pyg=valor_pyg, total_a_cobrar_entregar=total,
    )
