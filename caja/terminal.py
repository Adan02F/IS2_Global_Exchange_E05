# -*- coding: utf-8 -*-
"""
Terminal de autoservicio (TAUser) para operaciones en efectivo ("metálico").

Flujo:

1. El cliente genera la operación desde la web (``generar_operacion``): elige si vende o compra divisas,
   la divisa y el monto. Recibe un código de 8 dígitos válido por ``TERMINAL_VALIDEZ_MINUTOS``.
2. En la terminal ingresa el código y su documento (``buscar_operacion``). Si la cotización cambió desde
   que generó el código, debe aceptar la nueva (``actualizar_cotizacion``) o cancelar (``cancelar_operacion``).
3. Deposita los billetes (``completar_operacion``): la terminal valida el monto, guarda los billetes en su
   stock, entrega el monto a recibir y el vuelto con los billetes disponibles y registra la transacción
   de compra o venta (``EFECTIVO_TERMINAL``), que entra en reportes, tablero de ganancias y facturación.

Los montos en efectivo se ajustan a billetes: el monto en Guaraníes se redondea al billete más chico
(1.000 Gs) a favor de la casa, y el monto en divisa extranjera debe poder formarse con billetes.
"""
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import gcd

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction

from .cotizacion import beneficio_de_cliente, cotizar_efectivo
from .models import DenominacionDivisa, OperacionTerminal, StockBilleteTerminal, TerminalAutoservicio

PAGO_TERMINAL = 'EFECTIVO_TERMINAL'
MARCA_TERMINAL = 'Terminal Autoservicio:'
MAXIMO_UNIDADES_DESGLOSE = 1_000_000


class TerminalError(Exception):
    """Error de negocio de la terminal: el mensaje se muestra tal cual al cliente."""


def validez_minutos():
    """Minutos de validez del código de operación (configurable con ``TERMINAL_VALIDEZ_MINUTOS``)."""
    return int(getattr(settings, 'TERMINAL_VALIDEZ_MINUTOS', 30))


# ---------------------------------------------------------------------------
# Billetes
# ---------------------------------------------------------------------------

def denominaciones(moneda):
    """
    Denominaciones activas de una moneda, de mayor a menor.

    Args:
        moneda (str): Código de la divisa.

    Returns:
        list[DenominacionDivisa]: Denominaciones activas.
    """
    return list(DenominacionDivisa.objects.filter(divisa=moneda, activa=True).order_by('-valor_facial'))


def billete_minimo(moneda):
    """
    Valor del billete más chico de la moneda.

    Args:
        moneda (str): Código de la divisa.

    Returns:
        Decimal | None: Valor facial, o None si la moneda no tiene billetes cargados.
    """
    valores = [d.valor_facial for d in denominaciones(moneda)]
    return min(valores) if valores else None


def desglosar(monto, disponibles):
    """
    Arma un monto con billetes, respetando cuántos hay de cada valor. Usa primero los billetes más grandes
    y, si con eso no alcanza, busca otra combinación posible.

    Args:
        monto (Decimal): Monto a formar.
        disponibles (dict[Decimal, int | None]): Billetes disponibles por valor facial
            (``None`` = cantidad ilimitada, para validar si un monto es formable).

    Returns:
        dict[Decimal, int] | None: Cantidad de billetes por valor, o None si no se puede formar.
    """
    monto = Decimal(monto)
    if monto == 0:
        return {}
    if monto < 0 or monto != monto.to_integral_value():
        return None
    valores = sorted((Decimal(v) for v in disponibles if disponibles[v] != 0), reverse=True)
    if not valores or any(v != v.to_integral_value() for v in valores):
        return None

    # 1) Billetes más grandes primero (resuelve casi todos los casos).
    resto, resultado = monto, {}
    for valor in valores:
        maximo = disponibles[valor]
        cantidad = int(resto // valor)
        if maximo is not None:
            cantidad = min(cantidad, maximo)
        if cantidad:
            resultado[valor] = cantidad
            resto -= valor * cantidad
    if resto == 0:
        return resultado

    # 2) Búsqueda exacta (cambio con cantidades limitadas) sobre la unidad común de los billetes.
    unidad = 0
    for valor in valores:
        unidad = gcd(unidad, int(valor))
    if int(monto) % unidad:
        return None
    objetivo = int(monto) // unidad
    if objetivo > MAXIMO_UNIDADES_DESGLOSE:
        return None
    pasos = [int(v) // unidad for v in valores]
    alcanzado = [-1] * (objetivo + 1)  # índice del billete usado para llegar a cada monto
    alcanzado[0] = len(valores)
    for indice, paso in enumerate(pasos):
        maximo = disponibles[valores[indice]]
        usados = [0] * (objetivo + 1)
        for x in range(paso, objetivo + 1):
            if alcanzado[x] == -1 and alcanzado[x - paso] != -1 and (maximo is None or usados[x - paso] < maximo):
                alcanzado[x] = indice
                usados[x] = usados[x - paso] + 1
    if alcanzado[objetivo] == -1:
        return None
    resultado, x = {}, objetivo
    while x > 0:
        indice = alcanzado[x]
        resultado[valores[indice]] = resultado.get(valores[indice], 0) + 1
        x -= pasos[indice]
    return resultado


def es_formable(monto, moneda):
    """
    Indica si un monto puede formarse con los billetes de la moneda (sin límite de cantidad).

    Args:
        monto (Decimal): Monto a validar.
        moneda (str): Código de la divisa.

    Returns:
        bool: True si existe una combinación de billetes.
    """
    return desglosar(monto, {d.valor_facial: None for d in denominaciones(moneda)}) is not None


def stock_de(terminal, moneda, bloquear=False):
    """
    Stock de billetes de la terminal para una moneda.

    Args:
        terminal (TerminalAutoservicio): Terminal.
        moneda (str): Código de la divisa.
        bloquear (bool): Si es True bloquea las filas (``select_for_update``) dentro de la transacción.

    Returns:
        dict[Decimal, StockBilleteTerminal]: Filas de stock por valor facial.
    """
    filas = StockBilleteTerminal.objects.filter(terminal=terminal, denominacion__divisa=moneda, denominacion__activa=True)
    if bloquear:
        filas = filas.select_for_update()
    return {fila.denominacion.valor_facial: fila for fila in filas.select_related('denominacion')}


def asegurar_stock(terminal):
    """
    Crea en cero las filas de stock que falten para todas las denominaciones activas.

    Args:
        terminal (TerminalAutoservicio): Terminal.
    """
    for denominacion in DenominacionDivisa.objects.filter(activa=True):
        StockBilleteTerminal.objects.get_or_create(terminal=terminal, denominacion=denominacion)


def _a_json(billetes):
    """Convierte ``{Decimal: cantidad}`` a ``{"100": cantidad}`` para guardarlo en un JSONField."""
    return {f'{valor.normalize():f}': cantidad for valor, cantidad in sorted(billetes.items(), reverse=True) if cantidad}


# ---------------------------------------------------------------------------
# Cotización ajustada a billetes
# ---------------------------------------------------------------------------

@dataclass
class CotizacionTerminal:
    """
    Cotización de una operación de terminal con los montos ya ajustados a billetes.

    Attributes:
        tipo_operacion (str): ``COMPRA`` o ``VENTA``.
        moneda_origen (str): Moneda que deposita el cliente.
        moneda_destino (str): Moneda que recibe el cliente.
        monto_origen (Decimal): Monto a depositar.
        monto_destino (Decimal): Monto a recibir.
        tasa (Decimal): Tasa aplicada.
        tasa_std (Decimal): Tasa estándar.
        benefit_percentage (Decimal): Beneficio por categoría (%).
        comision (Decimal): Comisión en la moneda de origen.
        valor_pyg (Decimal): Valor de la operación en Guaraníes.
        redondeo_pyg (Decimal): Ajuste a billetes de Guaraníes a favor de la casa.
    """
    tipo_operacion: str
    moneda_origen: str
    moneda_destino: str
    monto_origen: Decimal
    monto_destino: Decimal
    tasa: Decimal
    tasa_std: Decimal
    benefit_percentage: Decimal
    comision: Decimal
    valor_pyg: Decimal
    redondeo_pyg: Decimal


def cotizar(cliente, tipo_operacion, divisa, monto):
    """
    Cotiza una operación de terminal y ajusta los montos a billetes.

    * ``COMPRA`` (el cliente vende divisas): ``monto`` es la divisa que deposita; recibe Guaraníes
      redondeados hacia abajo al billete más chico.
    * ``VENTA`` (el cliente compra divisas): ``monto`` es la divisa que recibe; deposita Guaraníes
      redondeados hacia arriba al billete más chico.

    Args:
        cliente (Cliente): Cliente de la operación.
        tipo_operacion (str): ``COMPRA`` o ``VENTA``.
        divisa (str): Divisa extranjera (USD, EUR, BRL, ARS).
        monto (Decimal): Monto en la divisa extranjera.

    Returns:
        CotizacionTerminal: Cotización ajustada.

    Raises:
        TerminalError: Si los datos no son válidos o el monto no se puede formar con billetes.
    """
    if tipo_operacion not in ('COMPRA', 'VENTA'):
        raise TerminalError('Tipo de operación no válido.')
    if not divisa or divisa == 'PYG':
        raise TerminalError('Seleccione una divisa extranjera.')
    try:
        monto = Decimal(monto)
    except Exception:
        raise TerminalError('Ingrese un monto válido.')
    if monto <= 0:
        raise TerminalError('El monto debe ser mayor a cero.')
    if not denominaciones(divisa):
        raise TerminalError(f'La terminal no opera billetes de {divisa}.')
    if not es_formable(monto, divisa):
        minimo = billete_minimo(divisa)
        raise TerminalError(
            f'El monto de {divisa} debe poder pagarse con billetes (sin centavos; billete más chico: {minimo.normalize():f} {divisa}).'
        )
    billete_pyg = billete_minimo('PYG') or Decimal('1')

    benefit_percentage, _ = beneficio_de_cliente(cliente)
    if tipo_operacion == 'COMPRA':
        moneda_origen, moneda_destino = divisa, 'PYG'
    else:
        moneda_origen, moneda_destino = 'PYG', divisa
    base = cotizar_efectivo(tipo_operacion, moneda_origen, moneda_destino, monto, benefit_percentage)

    if tipo_operacion == 'COMPRA':
        exacto = base.monto_destino
        ajustado = (exacto / billete_pyg).to_integral_value(rounding=ROUND_FLOOR) * billete_pyg
        monto_origen, monto_destino, redondeo = monto, ajustado, exacto - ajustado
        if monto_destino <= 0:
            raise TerminalError('El monto es demasiado chico para entregarlo en billetes de Guaraníes.')
    else:
        exacto = base.monto_origen
        ajustado = (exacto / billete_pyg).to_integral_value(rounding=ROUND_CEILING) * billete_pyg
        monto_origen, monto_destino, redondeo = ajustado, monto, ajustado - exacto

    return CotizacionTerminal(
        tipo_operacion=tipo_operacion, moneda_origen=moneda_origen, moneda_destino=moneda_destino,
        monto_origen=monto_origen.quantize(Decimal('0.01')), monto_destino=monto_destino.quantize(Decimal('0.01')),
        tasa=base.tasa, tasa_std=base.tasa_std, benefit_percentage=benefit_percentage,
        comision=base.comision.quantize(Decimal('0.01')), valor_pyg=base.valor_pyg.quantize(Decimal('0.01')),
        redondeo_pyg=redondeo.quantize(Decimal('0.01')),
    )


def _aplicar_cotizacion(operacion, cotizacion):
    """Copia los montos de una cotización en la operación (sin guardar)."""
    for campo in ('tipo_operacion', 'moneda_origen', 'moneda_destino', 'monto_origen', 'monto_destino',
                  'benefit_percentage', 'comision', 'valor_pyg', 'redondeo_pyg'):
        setattr(operacion, campo, getattr(cotizacion, campo))
    operacion.tasa_aplicada = cotizacion.tasa
    operacion.tasa_estandar = cotizacion.tasa_std


# ---------------------------------------------------------------------------
# Ciclo de vida de la operación
# ---------------------------------------------------------------------------

def _nuevo_codigo():
    """Código numérico de 8 dígitos que no está en uso por otra operación pendiente."""
    for _ in range(20):
        codigo = f'{secrets.randbelow(10 ** 8):08d}'
        if not OperacionTerminal.objects.filter(codigo=codigo, estado=OperacionTerminal.PENDIENTE).exists():
            return codigo
    raise TerminalError('No se pudo generar un código de operación. Intente nuevamente.')


def generar_operacion(usuario, cliente, tipo_operacion, divisa, monto):
    """
    Crea una operación pendiente y su código para completarla en una terminal.

    Args:
        usuario (User): Usuario que la genera.
        cliente (Cliente): Cliente activo.
        tipo_operacion (str): ``COMPRA`` (vender divisas) o ``VENTA`` (comprar divisas).
        divisa (str): Divisa extranjera.
        monto (Decimal): Monto en la divisa extranjera.

    Returns:
        OperacionTerminal: Operación pendiente con su código.

    Raises:
        TerminalError: Si no hay cliente o la cotización no es válida.
    """
    if cliente is None:
        raise TerminalError('Debe seleccionar un cliente activo para operar en la terminal.')
    cotizacion = cotizar(cliente, tipo_operacion, divisa, monto)
    operacion = OperacionTerminal(
        codigo=_nuevo_codigo(), cliente=cliente, usuario=usuario,
        expira_en=timezone.now() + timedelta(minutes=validez_minutos()),
    )
    _aplicar_cotizacion(operacion, cotizacion)
    operacion.save()
    return operacion


def vencer_operaciones():
    """
    Marca como vencidas las operaciones pendientes cuyo código ya expiró.

    Returns:
        int: Cantidad de operaciones vencidas.
    """
    return OperacionTerminal.objects.filter(
        estado=OperacionTerminal.PENDIENTE, expira_en__lt=timezone.now()
    ).update(estado=OperacionTerminal.VENCIDA)


def buscar_operacion(codigo, documento):
    """
    Busca la operación pendiente que el cliente quiere completar en la terminal.

    Args:
        codigo (str): Código de 8 dígitos.
        documento (str): Cédula o RUC del cliente (segundo factor).

    Returns:
        OperacionTerminal: Operación pendiente y vigente.

    Raises:
        TerminalError: Si no existe, no coincide el documento, venció o ya no está pendiente.
    """
    codigo = (codigo or '').strip()
    documento = (documento or '').strip()
    vencer_operaciones()
    operacion = (
        OperacionTerminal.objects.filter(codigo=codigo, cliente__documento_identidad__iexact=documento)
        .select_related('cliente').order_by('-creado_en').first()
    )
    if operacion is None or not codigo or not documento:
        raise TerminalError('Código o documento incorrectos.')
    if operacion.estado == OperacionTerminal.VENCIDA:
        raise TerminalError('El código venció. Genere una nueva operación desde la web.')
    if operacion.estado != OperacionTerminal.PENDIENTE:
        raise TerminalError(f'La operación ya está {operacion.get_estado_display().lower()}.')
    return operacion


def cotizacion_cambio(operacion):
    """
    Recalcula la operación con la cotización vigente.

    Args:
        operacion (OperacionTerminal): Operación pendiente.

    Returns:
        CotizacionTerminal | None: Nueva cotización si los montos cambiaron, o None si sigue igual.
    """
    monto = operacion.monto_origen if operacion.tipo_operacion == 'COMPRA' else operacion.monto_destino
    nueva = cotizar(operacion.cliente, operacion.tipo_operacion, operacion.divisa_extranjera, monto)
    if nueva.monto_origen == operacion.monto_origen and nueva.monto_destino == operacion.monto_destino:
        return None
    return nueva


def actualizar_cotizacion(operacion):
    """
    Aplica la cotización vigente a una operación pendiente (el cliente aceptó el cambio de tasa).

    Args:
        operacion (OperacionTerminal): Operación pendiente.

    Returns:
        OperacionTerminal: Operación actualizada.
    """
    nueva = cotizacion_cambio(operacion)
    if nueva is not None:
        _aplicar_cotizacion(operacion, nueva)
        operacion.save()
    return operacion


def cancelar_operacion(operacion, motivo):
    """
    Cancela una operación pendiente.

    Args:
        operacion (OperacionTerminal): Operación a cancelar.
        motivo (str): Motivo de la cancelación.

    Raises:
        TerminalError: Si la operación ya no está pendiente.
    """
    if operacion.estado != OperacionTerminal.PENDIENTE:
        raise TerminalError('Solo se pueden cancelar operaciones pendientes.')
    operacion.estado = OperacionTerminal.CANCELADA
    operacion.motivo_cancelacion = (motivo or '')[:200]
    operacion.save(update_fields=['estado', 'motivo_cancelacion'])


def _leer_billetes(moneda, billetes):
    """
    Normaliza los billetes depositados a ``{Decimal: cantidad}`` y valida que existan en la moneda.

    Raises:
        TerminalError: Si hay cantidades negativas o denominaciones que no son de la moneda.
    """
    validos = {d.valor_facial for d in denominaciones(moneda)}
    resultado = {}
    for valor, cantidad in (billetes or {}).items():
        try:
            valor, cantidad = Decimal(str(valor)), int(cantidad or 0)
        except Exception:
            raise TerminalError('Cantidad de billetes no válida.')
        if cantidad < 0:
            raise TerminalError('La cantidad de billetes no puede ser negativa.')
        if cantidad == 0:
            continue
        if valor not in validos:
            raise TerminalError(f'La terminal no acepta billetes de {valor.normalize():f} {moneda}.')
        resultado[valor] = resultado.get(valor, 0) + cantidad
    return resultado


def _retirar(stock, billetes):
    """Descuenta billetes del stock bloqueado."""
    for valor, cantidad in billetes.items():
        fila = stock[valor]
        fila.cantidad -= cantidad
        fila.save(update_fields=['cantidad'])


def _registrar_transaccion(operacion):
    """
    Registra la compra o venta de la operación, igual que la ventanilla pero con ``EFECTIVO_TERMINAL``.

    Returns:
        CurrencyPurchaseTransaction | CurrencySaleTransaction: Transacción creada.
    """
    datos = dict(
        user=operacion.usuario, cliente=operacion.cliente, turno=None,
        from_currency=operacion.moneda_origen, to_currency=operacion.moneda_destino,
        amount=operacion.monto_origen, converted_amount=operacion.monto_destino,
        applied_rate=operacion.tasa_aplicada, standard_rate=operacion.tasa_estandar,
        benefit_percentage=operacion.benefit_percentage, commission_amount=operacion.comision,
        status='SUCCESS', payment_method_type=PAGO_TERMINAL, monto_moneda_local=operacion.valor_pyg,
        transparent_breakdown=(
            f"{MARCA_TERMINAL} {operacion.terminal.codigo} Operación: {operacion.codigo} | "
            f"Redondeo a billetes: {operacion.redondeo_pyg} PYG"
        ),
    )
    if operacion.tipo_operacion == 'COMPRA':
        return CurrencyPurchaseTransaction.objects.create(total_pyg=operacion.monto_destino, **datos)
    return CurrencySaleTransaction.objects.create(total_pyg=operacion.monto_origen, **datos)


def completar_operacion(operacion, terminal, billetes):
    """
    Completa la operación en la terminal: recibe los billetes, entrega el monto y el vuelto,
    actualiza el stock y registra la transacción. Todo o nada: si la terminal no puede entregar
    el dinero, no se modifica nada y el cliente retira sus billetes.

    Args:
        operacion (OperacionTerminal): Operación pendiente.
        terminal (TerminalAutoservicio): Terminal donde se completa.
        billetes (dict): Billetes depositados ``{valor_facial: cantidad}`` en la moneda de origen.

    Returns:
        tuple[OperacionTerminal, CurrencyPurchaseTransaction | CurrencySaleTransaction]: Operación completada y transacción.

    Raises:
        TerminalError: Si la terminal está inactiva, el depósito no alcanza o no hay billetes para entregar.
    """
    if not terminal.activa:
        raise TerminalError('La terminal está fuera de servicio.')
    depositados = _leer_billetes(operacion.moneda_origen, billetes)
    total_depositado = sum((valor * cantidad for valor, cantidad in depositados.items()), Decimal('0'))
    if total_depositado < operacion.monto_origen:
        raise TerminalError(
            f'Depositó {total_depositado:,.2f} {operacion.moneda_origen}; '
            f'debe depositar {operacion.monto_origen:,.2f} {operacion.moneda_origen}.'
        )
    vuelto = total_depositado - operacion.monto_origen

    if OperacionTerminal.objects.filter(
        pk=operacion.pk, estado=OperacionTerminal.PENDIENTE, expira_en__lt=timezone.now()
    ).update(estado=OperacionTerminal.VENCIDA):
        raise TerminalError('El código venció. Genere una nueva operación desde la web.')

    with transaction.atomic():
        operacion = OperacionTerminal.objects.select_for_update().get(pk=operacion.pk)
        if operacion.estado != OperacionTerminal.PENDIENTE:
            raise TerminalError(f'La operación ya está {operacion.get_estado_display().lower()}.')

        asegurar_stock(terminal)
        stock_origen = stock_de(terminal, operacion.moneda_origen, bloquear=True)
        stock_destino = stock_de(terminal, operacion.moneda_destino, bloquear=True)

        # Los billetes depositados entran al stock y pueden usarse para el vuelto.
        for valor, cantidad in depositados.items():
            stock_origen[valor].cantidad += cantidad
            stock_origen[valor].save(update_fields=['cantidad'])

        entrega = desglosar(operacion.monto_destino, {v: f.cantidad for v, f in stock_destino.items()})
        if entrega is None:  # la excepción revierte el depósito: el cliente retira sus billetes
            raise TerminalError(
                f'La terminal no tiene billetes de {operacion.moneda_destino} suficientes para entregar '
                f'{operacion.monto_destino:,.2f}. Retire sus billetes y diríjase a ventanilla.'
            )
        _retirar(stock_destino, entrega)

        entrega_vuelto = desglosar(vuelto, {v: f.cantidad for v, f in stock_origen.items()})
        if entrega_vuelto is None:
            raise TerminalError(
                f'La terminal no tiene billetes para entregar el vuelto de {vuelto:,.2f} {operacion.moneda_origen}. '
                'Retire sus billetes e intente con el monto exacto.'
            )
        _retirar(stock_origen, entrega_vuelto)

        operacion.terminal = terminal
        operacion.estado = OperacionTerminal.COMPLETADA
        operacion.efectivo_depositado = total_depositado
        operacion.vuelto = vuelto
        operacion.billetes_depositados = _a_json(depositados)
        operacion.billetes_entregados = {operacion.moneda_destino: _a_json(entrega)}
        if entrega_vuelto:
            operacion.billetes_entregados[f'vuelto_{operacion.moneda_origen}'] = _a_json(entrega_vuelto)
        operacion.completado_en = timezone.now()
        operacion.save()

        transaccion = _registrar_transaccion(operacion)
        cliente = operacion.cliente
        cliente.transaction_volume = Decimal(str(cliente.transaction_volume or 0)) + (
            operacion.monto_destino if operacion.tipo_operacion == 'COMPRA' else operacion.monto_origen
        )
        cliente.save(update_fields=['transaction_volume'])
    return operacion, transaccion


def recargar_stock(terminal, moneda, billetes, retirar=False):
    """
    Carga (o retira) billetes del stock de una terminal. Lo usa el administrador.

    Args:
        terminal (TerminalAutoservicio): Terminal.
        moneda (str): Código de la divisa.
        billetes (dict): ``{valor_facial: cantidad}``.
        retirar (bool): Si es True descuenta en lugar de sumar.

    Returns:
        Decimal: Monto total cargado o retirado.

    Raises:
        TerminalError: Si se intenta retirar más billetes de los que hay.
    """
    cantidades = _leer_billetes(moneda, billetes)
    with transaction.atomic():
        asegurar_stock(terminal)
        stock = stock_de(terminal, moneda, bloquear=True)
        for valor, cantidad in cantidades.items():
            fila = stock[valor]
            if retirar and fila.cantidad < cantidad:
                raise TerminalError(
                    f'Solo hay {fila.cantidad} billetes de {valor.normalize():f} {moneda} en la terminal.'
                )
            fila.cantidad = fila.cantidad - cantidad if retirar else fila.cantidad + cantidad
            fila.save(update_fields=['cantidad'])
    return sum((valor * cantidad for valor, cantidad in cantidades.items()), Decimal('0'))


def resumen_stock(terminal):
    """
    Stock de la terminal agrupado por moneda, para mostrarlo en pantalla.

    Args:
        terminal (TerminalAutoservicio): Terminal.

    Returns:
        list[dict]: Por moneda: ``moneda``, ``filas`` (StockBilleteTerminal) y ``total``.
    """
    asegurar_stock(terminal)
    grupos = {}
    for fila in StockBilleteTerminal.objects.filter(terminal=terminal, denominacion__activa=True).select_related('denominacion'):
        grupo = grupos.setdefault(fila.denominacion.divisa, {'moneda': fila.denominacion.divisa, 'filas': [], 'total': Decimal('0')})
        grupo['filas'].append(fila)
        grupo['total'] += fila.denominacion.valor_facial * fila.cantidad
    orden = ['PYG', 'USD', 'EUR', 'BRL', 'ARS']
    return sorted(grupos.values(), key=lambda g: orden.index(g['moneda']) if g['moneda'] in orden else len(orden))


def crear_terminal_demo():
    """
    Crea la terminal de demostración T01 con un stock inicial de billetes, si no existe.

    Returns:
        TerminalAutoservicio: Terminal T01.
    """
    terminal, creada = TerminalAutoservicio.objects.get_or_create(
        codigo='T01', defaults={'nombre': 'Terminal de Autoservicio 01', 'ubicacion': 'Casa Central'},
    )
    asegurar_stock(terminal)
    if creada:
        for fila in StockBilleteTerminal.objects.filter(terminal=terminal).select_related('denominacion'):
            fila.cantidad = 50
            fila.save(update_fields=['cantidad'])
    return terminal
