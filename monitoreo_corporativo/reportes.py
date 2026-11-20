# -*- coding: utf-8 -*-
"""
Reportes financieros de transacciones y ganancias (PSE-41).

Consolida en Guaraníes (PYG) las compras y ventas de divisas (online y de ventanilla),
permite filtrarlas de forma combinable y exportarlas a CSV (se abre en Excel) y a PDF
(archivo generado en el servidor con ReportLab). También se conserva la vista de impresión
del navegador (``window.print()``), igual que la planilla de cierre de caja.

Criterios contables aplicados:

* Solo se incluyen las operaciones con estado ``SUCCESS``. Las fallidas, pendientes,
  canceladas y anuladas (``ANULADA``, factura anulada con devolución) no generan ganancia.
* **Volumen operado** = ``total_pyg`` de la operación. En las operaciones cruzadas (ninguna moneda
  es PYG, ej. USD→EUR) ``total_pyg`` no está en Guaraníes, así que se usa el valor en PYG de la
  moneda de origen (``monto_moneda_local`` o monto × tasa de la pata de origen).
* **Ganancia neta** = (comisión − IVA) + margen cambiario, todo en PYG.

Moneda de la comisión (así la guardan los servicios de operaciones):

* Compra online (``CurrencyPurchaseTransaction``): moneda de origen (lo que entrega el cliente).
* Venta online (``CurrencySaleTransaction``): moneda de destino (el monto convertido).
* Efectivo de ventanilla (``EFECTIVO_VENTANILLA``) y terminal (``EFECTIVO_TERMINAL``): moneda de origen.

Si esa moneda no es PYG, se convierte con la tasa que se aplicó a esa moneda en la propia operación.

**Margen cambiario.** Cada operación se descompone en "patas" contra el Guaraní: la casa *compra*
la moneda que entrega el cliente y *vende* la que recibe. En una cruzada USD→EUR hay dos patas
(compra USD y vende EUR). Para cada pata:

* Casa compra la divisa X: ``margen = cantidad × (tasa media X − tasa pagada)``.
* Casa vende la divisa Y:  ``margen = cantidad × (tasa cobrada − tasa media Y)``.

La *tasa media* es el promedio compra/venta de pizarra vigente al momento de la operación
(``ExchangeRateHistory``; si no hay historial, la cotización actual). Como la tasa pagada/cobrada
ya incluye el **beneficio del cliente** (VIP, Corporativo), el margen queda neto de ese beneficio.
El beneficio cedido se informa aparte (``beneficio_pyg``) para ver cuánto se resignó.
"""
import bisect
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q
from django.utils import timezone

from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate, ExchangeRateHistory

TIPO_COMPRA = 'COMPRA'
TIPO_VENTA = 'VENTA'
TIPOS_OPERACION = [(TIPO_COMPRA, 'Compra'), (TIPO_VENTA, 'Venta')]

ESTADO_CONTABLE = 'SUCCESS'
PAGO_VENTANILLA = 'EFECTIVO_VENTANILLA'
PAGO_TERMINAL = 'EFECTIVO_TERMINAL'
PAGOS_EN_EFECTIVO = (PAGO_VENTANILLA, PAGO_TERMINAL)
CANALES = {PAGO_VENTANILLA: 'Ventanilla', PAGO_TERMINAL: 'Terminal'}

MONEDA_BASE = 'PYG'
CENTIMOS = Decimal('0.01')
CERO = Decimal('0.00')
UNO = Decimal('1')
CIEN = Decimal('100')

COLUMNAS_EXPORTACION = [
    'Fecha', 'Tipo', 'Canal', 'Cliente', 'Cajero / Usuario', 'Moneda Origen', 'Moneda Destino',
    'Monto Origen', 'Tasa Aplicada', 'Volumen (PYG)', 'Comisión (PYG)', 'IVA (PYG)',
    'Margen Cambiario (PYG)', 'Beneficio Cliente (PYG)', 'Ganancia Neta (PYG)',
]


@dataclass
class FiltrosReporte:
    """
    Filtros combinables del reporte. Un campo vacío (``None`` o ``''``) no filtra.

    Attributes:
        fecha_desde (date | None): Fecha inicial inclusive.
        fecha_hasta (date | None): Fecha final inclusive (se toma hasta el final del día).
        tipo (str): ``COMPRA``, ``VENTA`` o vacío para ambos.
        moneda (str): Código de divisa; coincide si es la moneda de origen o la de destino.
        cajero_id (int | None): Usuario cajero del turno de caja en que se registró la operación.
        cliente_id (str | None): UUID del cliente.
    """
    fecha_desde: object = None
    fecha_hasta: object = None
    tipo: str = ''
    moneda: str = ''
    cajero_id: object = None
    cliente_id: object = None


@dataclass
class FilaReporte:
    """
    Una operación del reporte con sus importes ya consolidados en PYG.

    Attributes:
        fecha (datetime): Fecha y hora de la operación (zona horaria local).
        tipo (str): ``COMPRA`` o ``VENTA``.
        canal (str): ``Ventanilla`` u ``Online``.
        cliente (str): Nombre o razón social del cliente.
        operador (str): Cajero del turno o, si no hay turno, el usuario que operó.
        moneda_origen (str): Moneda entregada por el cliente.
        moneda_destino (str): Moneda recibida por el cliente.
        monto_origen (Decimal): Monto en la moneda de origen.
        tasa_aplicada (Decimal): Tasa de cambio aplicada.
        volumen_pyg (Decimal): Volumen operado en PYG.
        comision_pyg (Decimal): Comisión en PYG.
        iva_pyg (Decimal): IVA de la comisión en PYG.
        ganancia_neta_pyg (Decimal): Comisión − IVA + margen cambiario, en PYG.
        margen_cambiario_pyg (Decimal): Margen por diferencia de tasas (ya neto del beneficio), en PYG.
        beneficio_pyg (Decimal): Beneficio cedido al cliente por su categoría, en PYG (informativo).
        ganancia_por_divisa (dict): Ganancia neta repartida por divisa extranjera: el margen de cada
            pata va a su divisa y la comisión neta a la divisa de la operación (en cruzadas, la de origen).
    """
    fecha: datetime
    tipo: str
    canal: str
    cliente: str
    operador: str
    moneda_origen: str
    moneda_destino: str
    monto_origen: Decimal
    tasa_aplicada: Decimal
    volumen_pyg: Decimal
    comision_pyg: Decimal
    iva_pyg: Decimal
    ganancia_neta_pyg: Decimal
    margen_cambiario_pyg: Decimal = field(default=CERO)
    beneficio_pyg: Decimal = field(default=CERO)
    ganancia_por_divisa: dict = field(default_factory=dict)

    def como_lista(self):
        """
        Devuelve la fila en el orden de ``COLUMNAS_EXPORTACION``.

        Returns:
            list: Valores de la fila (fecha formateada como texto).
        """
        return [
            self.fecha.strftime('%d/%m/%Y %H:%M'), self.tipo.capitalize(), self.canal, self.cliente,
            self.operador, self.moneda_origen, self.moneda_destino, self.monto_origen, self.tasa_aplicada,
            self.volumen_pyg, self.comision_pyg, self.iva_pyg, self.margen_cambiario_pyg,
            self.beneficio_pyg, self.ganancia_neta_pyg,
        ]


@dataclass
class TotalesReporte:
    """
    Totales acumulados del período filtrado.

    Attributes:
        cantidad (int): Cantidad de operaciones.
        cantidad_compras (int): Cantidad de compras.
        cantidad_ventas (int): Cantidad de ventas.
        volumen_compras_pyg (Decimal): Volumen de compras en PYG.
        volumen_ventas_pyg (Decimal): Volumen de ventas en PYG.
        volumen_total_pyg (Decimal): Volumen total operado en PYG.
        comision_pyg (Decimal): Comisiones acumuladas en PYG.
        iva_pyg (Decimal): IVA acumulado en PYG.
        margen_cambiario_pyg (Decimal): Margen cambiario acumulado en PYG.
        beneficio_pyg (Decimal): Beneficios cedidos a clientes en PYG.
        ganancia_neta_pyg (Decimal): Ganancia neta del período en PYG.
    """
    cantidad: int = 0
    cantidad_compras: int = 0
    cantidad_ventas: int = 0
    volumen_compras_pyg: Decimal = field(default=CERO)
    volumen_ventas_pyg: Decimal = field(default=CERO)
    volumen_total_pyg: Decimal = field(default=CERO)
    comision_pyg: Decimal = field(default=CERO)
    iva_pyg: Decimal = field(default=CERO)
    margen_cambiario_pyg: Decimal = field(default=CERO)
    beneficio_pyg: Decimal = field(default=CERO)
    ganancia_neta_pyg: Decimal = field(default=CERO)


def _redondear(valor):
    """Redondea un importe a dos decimales (mitad hacia arriba)."""
    return Decimal(valor or 0).quantize(CENTIMOS, rounding=ROUND_HALF_UP)


def _decimal(valor):
    """Convierte a Decimal; ``None`` queda como ``None``."""
    return None if valor is None else Decimal(str(valor))


class CotizacionesHistoricas:
    """
    Cotizaciones de pizarra (compra/venta en PYG) vigentes en cada momento.

    Se cargan una sola vez por reporte desde ``ExchangeRateHistory``; para un momento dado se usa el
    último registro anterior o igual. Si una divisa no tiene historial previo, se usa su cotización
    actual de ``ExchangeRate``.
    """

    def __init__(self, monedas, hasta=None):
        """
        Args:
            monedas (Iterable[str]): Divisas involucradas (PYG se ignora).
            hasta (datetime | None): Momento máximo a cargar del historial.
        """
        monedas = {m for m in monedas if m and m != MONEDA_BASE}
        self._momentos = {}
        self._tasas = {}
        historial = ExchangeRateHistory.objects.filter(currency_code__in=monedas)
        if hasta is not None:
            historial = historial.filter(timestamp__lte=hasta)
        for registro in historial.order_by('currency_code', 'timestamp'):
            self._momentos.setdefault(registro.currency_code, []).append(registro.timestamp)
            self._tasas.setdefault(registro.currency_code, []).append((registro.buy_rate, registro.sell_rate))
        self._vigentes = {
            t.currency_code: (t.buy_rate, t.sell_rate)
            for t in ExchangeRate.objects.filter(currency_code__in=monedas)
        }

    def compra_venta(self, moneda, momento):
        """
        Tasas de compra y venta de pizarra vigentes en ``momento``.

        Returns:
            tuple[Decimal, Decimal] | None: (compra, venta); ``(1, 1)`` para PYG; None si no hay datos.
        """
        if moneda == MONEDA_BASE:
            return UNO, UNO
        momentos = self._momentos.get(moneda)
        if momentos:
            posicion = bisect.bisect_right(momentos, momento) - 1
            if posicion >= 0:
                return self._tasas[moneda][posicion]
        return self._vigentes.get(moneda)

    def media(self, moneda, momento):
        """
        Tasa media de pizarra ``(compra + venta) / 2`` vigente en ``momento``.

        Returns:
            Decimal | None: Tasa media; None si no hay cotización o es inválida.
        """
        tasas = self.compra_venta(moneda, momento)
        if not tasas or not tasas[0] or not tasas[1]:
            return None
        return (Decimal(tasas[0]) + Decimal(tasas[1])) / 2


def es_cruzada(transaccion):
    """True si ninguna de las dos monedas de la operación es PYG (ej. USD→EUR)."""
    return transaccion.from_currency != MONEDA_BASE and transaccion.to_currency != MONEDA_BASE


def moneda_comision(transaccion):
    """
    Moneda en la que el servicio de operaciones guardó la comisión y el IVA.

    Args:
        transaccion (CurrencyPurchaseTransaction | CurrencySaleTransaction): Operación.

    Returns:
        str: Código de moneda.
    """
    if transaccion.payment_method_type in PAGOS_EN_EFECTIVO:
        return transaccion.from_currency
    if isinstance(transaccion, CurrencySaleTransaction):
        return transaccion.to_currency
    return transaccion.from_currency


def tasas_por_pata(transaccion, cotizaciones=None):
    """
    Tasa en PYG por unidad que efectivamente se aplicó a cada divisa extranjera de la operación.

    * Con una sola divisa extranjera, es la ``applied_rate`` guardada (ya incluye el beneficio).
    * En cruzadas, ``applied_rate`` es la tasa cruzada, por eso cada pata se reconstruye desde el
      valor en PYG de la operación (``monto_moneda_local``). En efectivo la comisión está incluida en
      los montos: se descuenta (compra) o se suma (venta) para obtener la tasa de la pata de destino.
    * Si faltan datos, se usa la pizarra del momento con el beneficio del cliente.

    Args:
        transaccion: Operación.
        cotizaciones (CotizacionesHistoricas | None): Pizarra histórica, para el último recurso.

    Returns:
        dict: ``{'origen': Decimal | None, 'destino': Decimal | None}`` (None si esa pata es PYG).
    """
    origen, destino = transaccion.from_currency, transaccion.to_currency
    aplicada = _decimal(transaccion.applied_rate) or CERO
    if not es_cruzada(transaccion):
        return {
            'origen': aplicada if origen != MONEDA_BASE else None,
            'destino': aplicada if destino != MONEDA_BASE else None,
        }

    monto = _decimal(transaccion.amount) or CERO
    convertido = _decimal(transaccion.converted_amount) or CERO
    local = _decimal(getattr(transaccion, 'monto_moneda_local', None))
    efectivo = transaccion.payment_method_type in PAGOS_EN_EFECTIVO
    tasa_origen = tasa_destino = None

    if local and monto:
        tasa_origen = local / monto
    elif efectivo and aplicada:
        tasa_origen = aplicada

    if tasa_origen and convertido:
        if efectivo:
            if local:
                comision_pyg = (_decimal(transaccion.commission_amount) or CERO) * tasa_origen
                ajuste = -comision_pyg if isinstance(transaccion, CurrencyPurchaseTransaction) else comision_pyg
                tasa_destino = (local + ajuste) / convertido
        elif aplicada:
            tasa_destino = tasa_origen / aplicada

    if (tasa_origen is None or tasa_destino is None) and cotizaciones is not None:
        beneficio = (_decimal(transaccion.benefit_percentage) or CERO) / CIEN
        momento = transaccion.timestamp
        pizarra_origen = cotizaciones.compra_venta(origen, momento)
        pizarra_destino = cotizaciones.compra_venta(destino, momento)
        if tasa_origen is None and pizarra_origen:
            tasa_origen = Decimal(pizarra_origen[0]) * (UNO + beneficio)
        if tasa_destino is None and pizarra_destino:
            tasa_destino = Decimal(pizarra_destino[1]) * (UNO - beneficio)

    return {'origen': tasa_origen, 'destino': tasa_destino}


def factor_comision_a_pyg(transaccion, cotizaciones=None):
    """
    Factor que convierte la comisión y el IVA guardados en la transacción a Guaraníes.

    Es la tasa que se aplicó a la moneda de la comisión en la propia operación (1 si es PYG).

    Args:
        transaccion (CurrencyPurchaseTransaction | CurrencySaleTransaction): Operación.
        cotizaciones (CotizacionesHistoricas | None): Pizarra histórica (último recurso).

    Returns:
        Decimal: Multiplicador a aplicar.
    """
    moneda = moneda_comision(transaccion)
    if moneda == MONEDA_BASE:
        return UNO
    tasas = tasas_por_pata(transaccion, cotizaciones)
    tasa = tasas['origen'] if moneda == transaccion.from_currency else tasas['destino']
    if tasa:
        return tasa
    media = cotizaciones.media(moneda, transaccion.timestamp) if cotizaciones else None
    return media or CERO


def margen_y_beneficio(transaccion, cotizaciones):
    """
    Margen cambiario y beneficio cedido de cada pata de la operación, en PYG.

    Args:
        transaccion: Operación.
        cotizaciones (CotizacionesHistoricas): Pizarra histórica para la tasa media.

    Returns:
        tuple[dict, dict]: ``({divisa: margen}, {divisa: beneficio})``. El margen es negativo si la
        tasa aplicada (con beneficio) quedó del lado desfavorable de la tasa media.
    """
    tasas = tasas_por_pata(transaccion, cotizaciones)
    beneficio_pct = (_decimal(transaccion.benefit_percentage) or CERO) / CIEN
    momento = transaccion.timestamp
    margenes, beneficios = {}, {}
    patas = (
        # (divisa, cantidad, tasa aplicada, la casa compra esa divisa)
        (transaccion.from_currency, _decimal(transaccion.amount) or CERO, tasas['origen'], True),
        (transaccion.to_currency, _decimal(transaccion.converted_amount) or CERO, tasas['destino'], False),
    )
    for divisa, cantidad, tasa, casa_compra in patas:
        if divisa == MONEDA_BASE or not tasa or not cantidad:
            continue
        media = cotizaciones.media(divisa, momento)
        if media is not None:
            diferencia = (media - tasa) if casa_compra else (tasa - media)
            margenes[divisa] = margenes.get(divisa, CERO) + cantidad * diferencia
        if beneficio_pct > 0:
            # tasa = pizarra × (1 ± b)  →  beneficio por unidad = tasa − pizarra
            if casa_compra:
                cedido = tasa - tasa / (UNO + beneficio_pct)
            else:
                cedido = (tasa / (UNO - beneficio_pct) - tasa) if beneficio_pct < UNO else CERO
            beneficios[divisa] = beneficios.get(divisa, CERO) + cantidad * cedido
    return margenes, beneficios


def volumen_pyg(transaccion, cotizaciones=None):
    """
    Volumen operado en PYG. En cruzadas ``total_pyg`` no está en Guaraníes y se recalcula.

    Returns:
        Decimal: Volumen sin redondear.
    """
    if not es_cruzada(transaccion):
        return _decimal(transaccion.total_pyg) or CERO
    local = _decimal(getattr(transaccion, 'monto_moneda_local', None))
    if local:
        return local
    tasa = tasas_por_pata(transaccion, cotizaciones)['origen'] or CERO
    return (_decimal(transaccion.amount) or CERO) * tasa


def _filtrar(queryset, filtros):
    """
    Aplica los filtros combinables a un queryset de compras o ventas.

    Args:
        queryset (QuerySet): Compras o ventas.
        filtros (FiltrosReporte): Filtros elegidos.

    Returns:
        QuerySet: Queryset filtrado (solo operaciones contabilizables).
    """
    queryset = queryset.filter(status=ESTADO_CONTABLE)
    zona = timezone.get_current_timezone()
    if filtros.fecha_desde:
        queryset = queryset.filter(timestamp__gte=timezone.make_aware(datetime.combine(filtros.fecha_desde, time.min), zona))
    if filtros.fecha_hasta:
        queryset = queryset.filter(timestamp__lte=timezone.make_aware(datetime.combine(filtros.fecha_hasta, time.max), zona))
    if filtros.moneda:
        queryset = queryset.filter(Q(from_currency=filtros.moneda) | Q(to_currency=filtros.moneda))
    if filtros.cajero_id:
        queryset = queryset.filter(turno__cajero_id=filtros.cajero_id)
    if filtros.cliente_id:
        queryset = queryset.filter(cliente_id=filtros.cliente_id)
    return queryset.select_related('cliente', 'user', 'turno__cajero')


def divisa_de_operacion(moneda_origen, moneda_destino):
    """
    Divisa extranjera a la que se atribuye la comisión: la distinta de PYG; en cruzadas, la de origen.

    Returns:
        str: Código de la divisa.
    """
    return moneda_origen if moneda_origen != MONEDA_BASE else moneda_destino


def _a_fila(transaccion, tipo, cotizaciones=None):
    """
    Convierte una transacción en una ``FilaReporte`` consolidada en PYG.

    Args:
        transaccion (CurrencyPurchaseTransaction | CurrencySaleTransaction): Operación.
        tipo (str): ``COMPRA`` o ``VENTA``.
        cotizaciones (CotizacionesHistoricas | None): Pizarra histórica; si no se pasa, se carga
            para las divisas de esta operación.

    Returns:
        FilaReporte: Fila del reporte.
    """
    if cotizaciones is None:
        cotizaciones = CotizacionesHistoricas({transaccion.from_currency, transaccion.to_currency})
    factor = factor_comision_a_pyg(transaccion, cotizaciones)
    comision = _redondear(Decimal(transaccion.commission_amount or 0) * factor)
    iva = _redondear(Decimal(transaccion.tax_amount or 0) * factor)
    margenes, beneficios = margen_y_beneficio(transaccion, cotizaciones)
    margenes = {d: _redondear(m) for d, m in margenes.items()}
    margen = sum(margenes.values(), CERO)
    beneficio = _redondear(sum(beneficios.values(), CERO))

    ganancia_por_divisa = dict(margenes)
    divisa = divisa_de_operacion(transaccion.from_currency, transaccion.to_currency)
    ganancia_por_divisa[divisa] = ganancia_por_divisa.get(divisa, CERO) + (comision - iva)

    operador = transaccion.turno.cajero if transaccion.turno_id else transaccion.user
    return FilaReporte(
        fecha=timezone.localtime(transaccion.timestamp),
        tipo=tipo,
        canal=CANALES.get(transaccion.payment_method_type, 'Online'),
        cliente=transaccion.cliente.nombre_o_razon_social if transaccion.cliente_id else '—',
        operador=operador.username if operador else '—',
        moneda_origen=transaccion.from_currency,
        moneda_destino=transaccion.to_currency,
        monto_origen=_redondear(transaccion.amount),
        tasa_aplicada=Decimal(transaccion.applied_rate or 0),
        volumen_pyg=_redondear(volumen_pyg(transaccion, cotizaciones)),
        comision_pyg=comision,
        iva_pyg=iva,
        ganancia_neta_pyg=comision - iva + margen,
        margen_cambiario_pyg=margen,
        beneficio_pyg=beneficio,
        ganancia_por_divisa=ganancia_por_divisa,
    )


def obtener_filas(filtros):
    """
    Devuelve las operaciones del reporte, de la más reciente a la más antigua.

    Args:
        filtros (FiltrosReporte): Filtros elegidos.

    Returns:
        list[FilaReporte]: Filas consolidadas en PYG.
    """
    operaciones = []
    if filtros.tipo in ('', None, TIPO_COMPRA):
        operaciones += [(t, TIPO_COMPRA) for t in _filtrar(CurrencyPurchaseTransaction.objects.all(), filtros)]
    if filtros.tipo in ('', None, TIPO_VENTA):
        operaciones += [(t, TIPO_VENTA) for t in _filtrar(CurrencySaleTransaction.objects.all(), filtros)]

    monedas = {m for t, _ in operaciones for m in (t.from_currency, t.to_currency)}
    hasta = max((t.timestamp for t, _ in operaciones), default=None)
    cotizaciones = CotizacionesHistoricas(monedas, hasta)

    filas = [_a_fila(t, tipo, cotizaciones) for t, tipo in operaciones]
    filas.sort(key=lambda f: f.fecha, reverse=True)
    return filas


def calcular_totales(filas):
    """
    Acumula volúmenes, comisiones, IVA y ganancia neta de las filas.

    Args:
        filas (list[FilaReporte]): Filas del reporte.

    Returns:
        TotalesReporte: Totales del período.
    """
    totales = TotalesReporte()
    for fila in filas:
        totales.cantidad += 1
        if fila.tipo == TIPO_COMPRA:
            totales.cantidad_compras += 1
            totales.volumen_compras_pyg += fila.volumen_pyg
        else:
            totales.cantidad_ventas += 1
            totales.volumen_ventas_pyg += fila.volumen_pyg
        totales.volumen_total_pyg += fila.volumen_pyg
        totales.comision_pyg += fila.comision_pyg
        totales.iva_pyg += fila.iva_pyg
        totales.margen_cambiario_pyg += fila.margen_cambiario_pyg
        totales.beneficio_pyg += fila.beneficio_pyg
        totales.ganancia_neta_pyg += fila.ganancia_neta_pyg
    return totales


def generar_reporte(filtros):
    """
    Genera las filas y los totales del reporte.

    Args:
        filtros (FiltrosReporte): Filtros elegidos.

    Returns:
        tuple[list[FilaReporte], TotalesReporte]: Filas y totales.
    """
    filas = obtener_filas(filtros)
    return filas, calcular_totales(filas)


def descripcion_filtros(filtros, cajero=None, cliente=None):
    """
    Texto legible de los filtros aplicados, para el encabezado de los archivos exportados.

    Args:
        filtros (FiltrosReporte): Filtros elegidos.
        cajero (User | None): Cajero filtrado, para mostrar su nombre.
        cliente (Cliente | None): Cliente filtrado, para mostrar su nombre.

    Returns:
        str: Descripción de los filtros, o "Sin filtros".
    """
    partes = []
    if filtros.fecha_desde:
        partes.append(f"Desde {filtros.fecha_desde.strftime('%d/%m/%Y')}")
    if filtros.fecha_hasta:
        partes.append(f"Hasta {filtros.fecha_hasta.strftime('%d/%m/%Y')}")
    if filtros.tipo:
        partes.append(f"Tipo: {filtros.tipo.capitalize()}")
    if filtros.moneda:
        partes.append(f"Moneda: {filtros.moneda}")
    if cajero is not None:
        partes.append(f"Cajero: {cajero.username}")
    if cliente is not None:
        partes.append(f"Cliente: {cliente.nombre_o_razon_social}")
    return ' | '.join(partes) if partes else 'Sin filtros'


def _filas_resumen(totales):
    """Filas (etiqueta, valor) del resumen de totales que se agregan al pie de cada archivo."""
    return [
        ('Cantidad de operaciones', totales.cantidad),
        ('Volumen compras (PYG)', totales.volumen_compras_pyg),
        ('Volumen ventas (PYG)', totales.volumen_ventas_pyg),
        ('Volumen total operado (PYG)', totales.volumen_total_pyg),
        ('Comisiones (PYG)', totales.comision_pyg),
        ('IVA sobre comisiones (PYG)', totales.iva_pyg),
        ('Margen cambiario (PYG)', totales.margen_cambiario_pyg),
        ('Beneficios cedidos a clientes (PYG)', totales.beneficio_pyg),
        ('Ganancia neta (PYG)', totales.ganancia_neta_pyg),
    ]


def exportar_csv(filas, totales, descripcion=''):
    """
    Genera el reporte en CSV (separador ``;`` y BOM UTF-8 para que Excel lo abra bien).

    Args:
        filas (list[FilaReporte]): Filas del reporte.
        totales (TotalesReporte): Totales del período.
        descripcion (str): Filtros aplicados.

    Returns:
        bytes: Contenido del archivo.
    """
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=';')
    escritor.writerow(['Global Exchange - Reporte de Transacciones y Ganancias (PYG)'])
    escritor.writerow([f'Filtros: {descripcion or "Sin filtros"}'])
    escritor.writerow([])
    escritor.writerow(COLUMNAS_EXPORTACION)
    for fila in filas:
        escritor.writerow(fila.como_lista())
    escritor.writerow([])
    escritor.writerow(['RESUMEN DE TOTALES'])
    for etiqueta, valor in _filas_resumen(totales):
        escritor.writerow([etiqueta, valor])
    return ('\ufeff' + salida.getvalue()).encode('utf-8')


def formatear_importe(valor, decimales=2):
    """
    Formatea un número al estilo paraguayo: punto de miles y coma decimal.

    Args:
        valor (Decimal | int | float): Importe a formatear.
        decimales (int): Cantidad de decimales a mostrar.

    Returns:
        str: Importe formateado, por ejemplo ``1.234.567,89``.
    """
    texto = f'{Decimal(valor or 0):,.{decimales}f}'
    return texto.replace(',', '_').replace('.', ',').replace('_', '.')


def exportar_pdf(filas, totales, descripcion='', generado=None):
    """
    Genera el reporte en PDF (A4 apaisado) con el detalle de operaciones y el resumen de totales.

    Args:
        filas (list[FilaReporte]): Filas del reporte.
        totales (TotalesReporte): Totales del período.
        descripcion (str): Filtros aplicados.
        generado (datetime | None): Fecha y hora de generación (por defecto, ahora).

    Returns:
        bytes: Contenido del archivo PDF.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    generado = generado or timezone.localtime()
    salida = io.BytesIO()
    documento = SimpleDocTemplate(
        salida, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title='Reporte de Transacciones y Ganancias', author='Global Exchange',
    )
    estilos = getSampleStyleSheet()
    celda = ParagraphStyle('celda', parent=estilos['Normal'], fontSize=6.5, leading=8)
    encabezado = ParagraphStyle('encabezado', parent=celda, textColor=colors.white, fontName='Helvetica-Bold')

    elementos = [
        Paragraph('Global Exchange - Reporte de Transacciones y Ganancias (PYG)', estilos['Title']),
        Paragraph(f'Filtros: {descripcion or "Sin filtros"}', estilos['Normal']),
        Paragraph(f"Generado: {generado.strftime('%d/%m/%Y %H:%M')}", estilos['Normal']),
        Spacer(1, 4 * mm),
    ]

    datos = [[Paragraph(columna, encabezado) for columna in COLUMNAS_EXPORTACION]]
    for fila in filas:
        datos.append([
            fila.fecha.strftime('%d/%m/%Y %H:%M'), fila.tipo.capitalize(), fila.canal,
            Paragraph(fila.cliente, celda), Paragraph(fila.operador, celda),
            fila.moneda_origen, fila.moneda_destino,
            formatear_importe(fila.monto_origen), formatear_importe(fila.tasa_aplicada, 4),
            formatear_importe(fila.volumen_pyg), formatear_importe(fila.comision_pyg),
            formatear_importe(fila.iva_pyg), formatear_importe(fila.margen_cambiario_pyg),
            formatear_importe(fila.beneficio_pyg), formatear_importe(fila.ganancia_neta_pyg),
        ])
    if not filas:
        datos.append(['Sin operaciones para los filtros seleccionados'] + [''] * (len(COLUMNAS_EXPORTACION) - 1))

    anchos = [22, 12, 15, 28, 20, 11, 11, 20, 17, 23, 19, 16, 21, 21, 21]
    tabla = Table(datos, colWidths=[a * mm for a in anchos], repeatRows=1)
    estilo_tabla = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
        ('FONTSIZE', (0, 0), (-1, -1), 6.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (7, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f1f5f9')]),
    ]
    if not filas:
        estilo_tabla.append(('SPAN', (0, 1), (-1, 1)))
    tabla.setStyle(TableStyle(estilo_tabla))
    elementos += [tabla, Spacer(1, 6 * mm)]

    resumen = [
        [etiqueta, str(valor) if isinstance(valor, int) else formatear_importe(valor)]
        for etiqueta, valor in _filas_resumen(totales)
    ]
    tabla_resumen = Table(resumen, colWidths=[70 * mm, 45 * mm], hAlign='LEFT')
    tabla_resumen.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cbd5e1')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#dcfce7')),
    ]))
    elementos.append(KeepTogether([Paragraph('Resumen de Totales', estilos['Heading2']), tabla_resumen]))

    documento.build(elementos)
    return salida.getvalue()
