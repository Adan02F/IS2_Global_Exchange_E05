# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal
from authentication.models import Cliente

class Caja(models.Model):
    """
    Modelo que representa una caja física en la casa de cambios.
    """
    nombre = models.CharField(max_length=100, unique=True, verbose_name="Nombre de Caja")
    codigo = models.CharField(max_length=50, unique=True, verbose_name="Código")
    activa = models.BooleanField(default=True, verbose_name="Activa")
    cajeros_asignados = models.ManyToManyField(User, blank=True, related_name='cajas_asignadas', verbose_name="Cajeros Asignados")

    def __str__(self):
        """
        Retorna la representación en cadena de la caja.
        """
        return f"{self.nombre} ({self.codigo})"

    class Meta:
        verbose_name = "Caja"
        verbose_name_plural = "Cajas"


class TurnoCaja(models.Model):
    """
    Modelo que gestiona la apertura y cierre de turnos de caja física,
    registrando saldos iniciales y finales detallados por divisa (USD, EUR, PYG, BRL, ARS)
    y controlando el estado activo para bloquear transacciones sin turno abierto.
    """
    ESTADO_CHOICES = [
        ('ABIERTO', 'Abierto'),
        ('CERRADO', 'Cerrado'),
    ]

    caja = models.ForeignKey(Caja, on_delete=models.CASCADE, verbose_name="Caja")
    cajero = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Cajero")
    fecha_apertura = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora de Apertura")
    fecha_cierre = models.DateTimeField(blank=True, null=True, verbose_name="Fecha y Hora de Cierre")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='ABIERTO', verbose_name="Estado de Turno")

    # Saldos Iniciales por Divisa
    saldo_inicial_pyg = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Saldo Inicial PYG")
    saldo_inicial_usd = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial USD")
    saldo_inicial_eur = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial EUR")
    saldo_inicial_brl = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial BRL")
    saldo_inicial_ars = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial ARS")

    # Saldos Finales por Divisa (al cerrar turno)
    saldo_final_pyg = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final PYG")
    saldo_final_usd = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final USD")
    saldo_final_eur = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final EUR")
    saldo_final_brl = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final BRL")
    saldo_final_ars = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final ARS")

    def __str__(self):
        """
        Retorna la representación descriptiva del turno de caja.
        """
        return f"Turno {self.id} - Caja: {self.caja.nombre} - Cajero: {self.cajero.username} ({self.estado})"

    def clean(self):
        """
        Valida que no exista otro turno abierto para la misma caja al intentar abrir.
        """
        super().clean()
        if self.estado == 'ABIERTO':
            qs = TurnoCaja.objects.filter(caja=self.caja, estado='ABIERTO')
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(f"La caja '{self.caja.nombre}' ya cuenta con un turno activo sin cerrar.")

    def save(self, *args, **kwargs):
        """
        Ejecuta clean antes de guardar.
        """
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Turno de Caja"
        verbose_name_plural = "Turnos de Caja"


class DenominacionDivisa(models.Model):
    """
    Modelo que representa una denominación de papel moneda autorizada por divisa (PSE-21).
    
    Attributes:
        divisa (CharField): Código ISO de la divisa (USD, PYG, EUR, BRL, ARS).
        valor_facial (DecimalField): Valor nominal del billete (ej. 100000 para PYG, 100 para USD).
        nombre (CharField): Nombre descriptivo de la denominación (ej. Billetes de 100.000 PYG).
        activa (BooleanField): Estado de habilitación.
    """
    divisa = models.CharField(max_length=10, verbose_name="Código de Divisa")
    valor_facial = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Valor Facial")
    nombre = models.CharField(max_length=100, verbose_name="Nombre de Denominación")
    activa = models.BooleanField(default=True, verbose_name="Activa")

    class Meta:
        verbose_name = "Denominación de Divisa"
        verbose_name_plural = "Denominaciones de Divisas"
        ordering = ['divisa', '-valor_facial']

    def __str__(self):
        """Devuelve la representación en cadena de la denominación."""
        return f"{self.nombre} ({self.divisa})"


class DesgloseEfectivoCaja(models.Model):
    """
    Modelo que representa un registro de conteo físico o movimiento de efectivo
    respaldado por el desglose detallado individual de billetes (PSE-21).
    
    Attributes:
        turno (TurnoCaja): Turno de caja asociado.
        tipo_operacion (CharField): APERTURA, CIERRE, INGRESO, SALIDA.
        divisa (CharField): Divisa del desglose.
        monto_total (DecimalField): Sumatoria total calculada del desglose.
        observacion (TextField): Notas u observaciones.
        fecha (DateTimeField): Fecha y hora del registro.
    """
    TIPO_OPERACION_CHOICES = [
        ('APERTURA', 'Apertura de Turno'),
        ('CIERRE', 'Cierre de Turno'),
        ('INGRESO', 'Ingreso Físico de Efectivo'),
        ('SALIDA', 'Salida Física de Efectivo'),
    ]

    turno = models.ForeignKey(TurnoCaja, on_delete=models.CASCADE, related_name='desgloses_efectivo', verbose_name="Turno de Caja")
    tipo_operacion = models.CharField(max_length=20, choices=TIPO_OPERACION_CHOICES, verbose_name="Tipo de Operación")
    divisa = models.CharField(max_length=10, verbose_name="Divisa")
    monto_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Monto Total")
    observacion = models.TextField(blank=True, null=True, verbose_name="Observación")
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora")

    class Meta:
        verbose_name = "Desglose de Efectivo de Caja"
        verbose_name_plural = "Desgloses de Efectivo de Caja"

    def __str__(self):
        """Devuelve la representación en cadena del desglose."""
        return f"{self.get_tipo_operacion_display()} - {self.divisa} {self.monto_total:,.2f} (Turno #{self.turno.id})"


class DetalleDesgloseBillete(models.Model):
    """
    Modelo que representa el detalle de cantidad de billetes por denominación
    en un desglose físico de efectivo (PSE-21).
    
    Attributes:
        desglose (DesgloseEfectivoCaja): Registro de desglose padre.
        denominacion (DenominacionDivisa): Denominación del billete.
        cantidad (IntegerField): Cantidad de billetes de dicha denominación.
        subtotal (DecimalField): Subtotal calculado (cantidad * valor_facial).
    """
    desglose = models.ForeignKey(DesgloseEfectivoCaja, on_delete=models.CASCADE, related_name='detalles', verbose_name="Desglose")
    denominacion = models.ForeignKey(DenominacionDivisa, on_delete=models.CASCADE, verbose_name="Denominación")
    cantidad = models.IntegerField(default=0, verbose_name="Cantidad de Billetes")
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Subtotal")

    class Meta:
        verbose_name = "Detalle de Billete"
        verbose_name_plural = "Detalles de Billetes"

    def save(self, *args, **kwargs):
        """Calcula automáticamente el subtotal multiplicando cantidad por valor facial antes de guardar."""
        self.subtotal = Decimal(str(self.cantidad)) * self.denominacion.valor_facial
        super().save(*args, **kwargs)

    def __str__(self):
        """Devuelve la representación en cadena del detalle de billete."""
        return f"{self.cantidad} x {self.denominacion.nombre} = {self.subtotal:,.2f}"


def inicializar_denominaciones_default():
    """Crea las denominaciones estándar por divisa si no existen."""
    denominaciones_data = [
        # PYG
        ('PYG', Decimal('100000'), 'Billete de 100.000 Guaraníes'),
        ('PYG', Decimal('50000'), 'Billete de 50.000 Guaraníes'),
        ('PYG', Decimal('20000'), 'Billete de 20.000 Guaraníes'),
        ('PYG', Decimal('10000'), 'Billete de 10.000 Guaraníes'),
        ('PYG', Decimal('5000'), 'Billete de 5.000 Guaraníes'),
        ('PYG', Decimal('2000'), 'Billete de 2.000 Guaraníes'),
        ('PYG', Decimal('1000'), 'Billete de 1.000 Guaraníes'),
        # USD
        ('USD', Decimal('100'), 'Billete de 100 USD'),
        ('USD', Decimal('50'), 'Billete de 50 USD'),
        ('USD', Decimal('20'), 'Billete de 20 USD'),
        ('USD', Decimal('10'), 'Billete de 10 USD'),
        ('USD', Decimal('5'), 'Billete de 5 USD'),
        ('USD', Decimal('1'), 'Billete de 1 USD'),
        # EUR
        ('EUR', Decimal('500'), 'Billete de 500 EUR'),
        ('EUR', Decimal('200'), 'Billete de 200 EUR'),
        ('EUR', Decimal('100'), 'Billete de 100 EUR'),
        ('EUR', Decimal('50'), 'Billete de 50 EUR'),
        ('EUR', Decimal('20'), 'Billete de 20 EUR'),
        ('EUR', Decimal('10'), 'Billete de 10 EUR'),
        ('EUR', Decimal('5'), 'Billete de 5 EUR'),
        # BRL
        ('BRL', Decimal('100'), 'Billete de 100 BRL'),
        ('BRL', Decimal('50'), 'Billete de 50 BRL'),
        ('BRL', Decimal('20'), 'Billete de 20 BRL'),
        ('BRL', Decimal('10'), 'Billete de 10 BRL'),
        ('BRL', Decimal('5'), 'Billete de 5 BRL'),
        ('BRL', Decimal('2'), 'Billete de 2 BRL'),
        # ARS
        ('ARS', Decimal('1000'), 'Billete de 1000 ARS'),
        ('ARS', Decimal('500'), 'Billete de 500 ARS'),
        ('ARS', Decimal('200'), 'Billete de 200 ARS'),
        ('ARS', Decimal('100'), 'Billete de 100 ARS'),
        ('ARS', Decimal('50'), 'Billete de 50 ARS'),
    ]
    for divisa, valor, nombre in denominaciones_data:
        DenominacionDivisa.objects.get_or_create(
            divisa=divisa,
            valor_facial=valor,
            defaults={'nombre': nombre, 'activa': True}
        )



def verificar_turno_activo(caja):
    """
    Verifica si una caja física posee un turno activo (abierto).
    Retorna el objeto TurnoCaja abierto o None.
    """
    return TurnoCaja.objects.filter(caja=caja, estado='ABIERTO').first()


def puede_realizar_transaccion(caja):
    """
    Retorna True si la caja posee un turno abierto, permitiendo la creación de transacciones.
    Caso contrario, retorna False (congelando/bloqueando operaciones).
    """
    if not caja:
        return False
    return TurnoCaja.objects.filter(caja=caja, estado='ABIERTO').exists()


def calcular_saldos_actuales_turno(turno):
    """
    Calcula el saldo actual de cada divisa en un turno activo (saldo inicial + ingresos - salidas).
    Retorna un diccionario {divisa: monto_actual}.
    """
    if not turno:
        return {}
    saldos = {
        'PYG': turno.saldo_inicial_pyg or Decimal('0.00'),
        'USD': turno.saldo_inicial_usd or Decimal('0.00'),
        'EUR': turno.saldo_inicial_eur or Decimal('0.00'),
        'BRL': turno.saldo_inicial_brl or Decimal('0.00'),
        'ARS': turno.saldo_inicial_ars or Decimal('0.00'),
    }
    desgloses = DesgloseEfectivoCaja.objects.filter(turno=turno)
    for des in desgloses:
        cur = des.divisa
        if cur not in saldos:
            saldos[cur] = Decimal('0.00')
        if des.tipo_operacion == 'INGRESO':
            saldos[cur] += des.monto_total
        elif des.tipo_operacion == 'SALIDA':
            saldos[cur] -= des.monto_total
    return saldos


class ArqueoCaja(models.Model):
    """
    Modelo que representa el resultado del arqueo automático de caja por divisa al cierre de turno (PSE-22).
    Compara el saldo inicial y movimientos del sistema contra el inventario físico de cierre.

    Attributes:
        turno (TurnoCaja): Turno de caja asociado.
        divisa (CharField): Código de la divisa (PYG, USD, EUR, BRL, ARS).
        saldo_inicial (DecimalField): Saldo físico inicial al abrir el turno.
        movimientos_sistema (DecimalField): Sumatoria neta de ingresos menos salidas físicas en el turno.
        saldo_teorico (DecimalField): Saldo esperado (saldo_inicial + movimientos_sistema).
        saldo_fisico_cierre (DecimalField): Conteo físico real declarado al cierre.
        diferencia (DecimalField): Desviación (saldo_fisico_cierre - saldo_teorico).
        estado_arqueo (CharField): Clasificación ('CUADRADO', 'FALTANTE', 'SOBRANTE').
        fecha (DateTimeField): Fecha y hora en que se ejecutó el arqueo.
    """
    ESTADO_ARQUEO_CHOICES = [
        ('CUADRADO', 'Cuadrado'),
        ('FALTANTE', 'Faltante'),
        ('SOBRANTE', 'Sobrante'),
    ]

    turno = models.ForeignKey(TurnoCaja, on_delete=models.CASCADE, related_name='arqueos', verbose_name="Turno de Caja")
    divisa = models.CharField(max_length=10, verbose_name="Divisa")
    saldo_inicial = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Saldo Inicial")
    movimientos_sistema = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Movimientos del Sistema")
    saldo_teorico = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Saldo Teórico Esperado")
    saldo_fisico_cierre = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Saldo Físico de Cierre")
    diferencia = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Diferencia")
    estado_arqueo = models.CharField(max_length=20, choices=ESTADO_ARQUEO_CHOICES, default='CUADRADO', verbose_name="Estado de Arqueo")
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Arqueo")

    class Meta:
        verbose_name = "Arqueo de Caja"
        verbose_name_plural = "Arqueos de Caja"
        ordering = ['-fecha', 'divisa']

    def __str__(self):
        """Retorna la representación en cadena del arqueo."""
        return f"Arqueo Turno #{self.turno.id} - {self.divisa}: {self.estado_arqueo} (Dif: {self.diferencia:,.2f})"


class BitacoraArqueo(models.Model):
    """
    Bitácora histórica de auditoría y notificaciones automáticas al Administrador ante descuadres en arqueos de caja (PSE-22).

    Attributes:
        turno (TurnoCaja): Turno de caja asociado.
        divisa (CharField): Divisa con discrepancia.
        tipo_descuadre (CharField): 'FALTANTE' o 'SOBRANTE'.
        monto_diferencia (DecimalField): Monto absoluto de la diferencia.
        mensaje (TextField): Detalle descriptivo de la alerta generada.
        administrador_notificado (BooleanField): Indica si se notificó al Administrador.
        fecha (DateTimeField): Fecha y hora de registro de la bitácora.
    """
    TIPO_DESCUADRE_CHOICES = [
        ('FALTANTE', 'Faltante'),
        ('SOBRANTE', 'Sobrante'),
    ]

    turno = models.ForeignKey(TurnoCaja, on_delete=models.CASCADE, related_name='bitacoras_arqueo', verbose_name="Turno de Caja")
    divisa = models.CharField(max_length=10, verbose_name="Divisa")
    tipo_descuadre = models.CharField(max_length=20, choices=TIPO_DESCUADRE_CHOICES, verbose_name="Tipo de Descuadre")
    monto_diferencia = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Monto de Diferencia")
    mensaje = models.TextField(verbose_name="Mensaje de Alerta / Bitácora")
    administrador_notificado = models.BooleanField(default=True, verbose_name="Administrador Notificado")
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora de Registro")

    class Meta:
        verbose_name = "Bitácora de Arqueo"
        verbose_name_plural = "Bitácoras de Arqueos"
        ordering = ['-fecha']

    def __str__(self):
        """Retorna la representación en cadena de la bitácora de arqueo."""
        return f"Bitácora Turno #{self.turno.id} - {self.divisa} {self.tipo_descuadre} ({self.monto_diferencia:,.2f})"


def procesar_arqueo_cierre(turno):
    """
    Ejecuta el arqueo automático de caja al cierre del turno para todas las divisas (PYG, USD, EUR, BRL, ARS),
    calculando saldo teórico vs físico, clasificando como Cuadrado, Faltante o Sobrante,
    registrando en la bitácora histórica y emitiendo notificación inmediata al Administrador ante descuadres (PSE-22).

    Args:
        turno (TurnoCaja): Objeto TurnoCaja que se está cerrando.
    """
    divisas_mapping = {
        'PYG': (turno.saldo_inicial_pyg, turno.saldo_final_pyg),
        'USD': (turno.saldo_inicial_usd, turno.saldo_final_usd),
        'EUR': (turno.saldo_inicial_eur, turno.saldo_final_eur),
        'BRL': (turno.saldo_inicial_brl, turno.saldo_final_brl),
        'ARS': (turno.saldo_inicial_ars, turno.saldo_final_ars),
    }

    for divisa, (saldo_inicial, saldo_final_fisico) in divisas_mapping.items():
        if saldo_inicial is None:
            saldo_inicial = Decimal('0.00')
        if saldo_final_fisico is None:
            saldo_final_fisico = Decimal('0.00')

        desgloses = DesgloseEfectivoCaja.objects.filter(turno=turno, divisa=divisa)
        movimientos = Decimal('0.00')
        for des in desgloses:
            if des.tipo_operacion == 'INGRESO':
                movimientos += des.monto_total
            elif des.tipo_operacion == 'SALIDA':
                movimientos -= des.monto_total

        saldo_teorico = saldo_inicial + movimientos
        diferencia = saldo_final_fisico - saldo_teorico

        if abs(diferencia) < Decimal('0.01'):
            estado = 'CUADRADO'
            diferencia = Decimal('0.00')
        elif diferencia < 0:
            estado = 'FALTANTE'
        else:
            estado = 'SOBRANTE'

        ArqueoCaja.objects.update_or_create(
            turno=turno,
            divisa=divisa,
            defaults={
                'saldo_inicial': saldo_inicial,
                'movimientos_sistema': movimientos,
                'saldo_teorico': saldo_teorico,
                'saldo_fisico_cierre': saldo_final_fisico,
                'diferencia': diferencia,
                'estado_arqueo': estado
            }
        )

        if estado != 'CUADRADO':
            tipo_desc = 'FALTANTE' if estado == 'FALTANTE' else 'SOBRANTE'
            monto_diff = abs(diferencia)
            mensaje = (
                f"ALERTA DE ARQUEO DE CAJA (#{turno.id}) - Caja: {turno.caja.nombre}, Cajero: {turno.cajero.username}. "
                f"Divisa: {divisa}. Descuadre detectado: {tipo_desc} de {divisa} {monto_diff:,.2f}. "
                f"Saldo Teórico Esperado: {saldo_teorico:,.2f} vs Conteo Físico Real: {saldo_final_fisico:,.2f}."
            )
            BitacoraArqueo.objects.create(
                turno=turno,
                divisa=divisa,
                tipo_descuadre=tipo_desc,
                monto_diferencia=monto_diff,
                mensaje=mensaje,
                administrador_notificado=True
            )


class OperacionVentanilla(models.Model):
    """
    Modelo que registra las operaciones presenciales de compra o venta de divisas en ventanilla (PSE-38),
    asociadas a un turno de caja activo, cliente presencial, cálculo de cotización, comisiones,
    vuelto en efectivo y actualización automática del saldo en caja.

    Attributes:
        turno (TurnoCaja): Turno de caja activo del cajero.
        cajero (User): Usuario cajero que atiende la operación.
        cliente (Cliente): Cliente presencial (identificado por CI/RUC o creado ocasionalmente).
        tipo_operacion (CharField): 'COMPRA' o 'VENTA'.
        moneda_origen (CharField): Código de moneda origen (ej. 'USD', 'PYG').
        moneda_destino (CharField): Código de moneda destino (ej. 'PYG', 'USD').
        monto_origen (DecimalField): Monto aportado por el cliente en moneda origen.
        monto_destino (DecimalField): Monto convertido a entregar al cliente.
        tasa_aplicada (DecimalField): Tasa de cambio vigente aplicada.
        comision (DecimalField): Monto de comisión aplicado.
        total_a_cobrar_entregar (DecimalField): Monto total neto en efectivo.
        efectivo_recibido (DecimalField): Efectivo físico recibido del cliente.
        vuelto (DecimalField): Vuelto en efectivo calculado a entregar.
        comprobante_numero (CharField): Número único del comprobante emitido.
        fecha (DateTimeField): Fecha y hora de la transacción.
    """
    TIPO_OPERACION_CHOICES = [
        ('COMPRA', 'Compra de Divisas'),
        ('VENTA', 'Venta de Divisas'),
    ]

    turno = models.ForeignKey(TurnoCaja, on_delete=models.CASCADE, related_name='operaciones_ventanilla', verbose_name="Turno de Caja")
    cajero = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Cajero")
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Cliente Presencial")
    tipo_operacion = models.CharField(max_length=20, choices=TIPO_OPERACION_CHOICES, verbose_name="Tipo de Operación")
    moneda_origen = models.CharField(max_length=10, verbose_name="Moneda Origen")
    moneda_destino = models.CharField(max_length=10, verbose_name="Moneda Destino")
    monto_origen = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Origen")
    monto_destino = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Destino")
    tasa_aplicada = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Aplicada")
    benefit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Beneficio por Categoría (%)")
    comision = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Comisión (Gs)")
    total_a_cobrar_entregar = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Total a Cobrar / Entregar")
    efectivo_recibido = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Efectivo Recibido")
    vuelto = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Vuelto en Efectivo")
    comprobante_numero = models.CharField(max_length=50, unique=True, verbose_name="Número de Comprobante")
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora de Operación")

    class Meta:
        verbose_name = "Operación de Ventanilla"
        verbose_name_plural = "Operaciones de Ventanilla"
        ordering = ['-fecha']

    def __str__(self):
        """Devuelve la representación en cadena de la operación de ventanilla."""
        return f"Ventanilla #{self.comprobante_numero} - {self.tipo_operacion}: {self.monto_origen} {self.moneda_origen} -> {self.monto_destino} {self.moneda_destino}"


class TerminalAutoservicio(models.Model):
    """
    Terminal de autoservicio (TAUser) para operaciones en efectivo ("metálico") sin cajero.

    El cliente genera un código de operación desde la web y en la terminal deposita y retira
    billetes. La terminal tiene su propio stock de billetes por denominación.

    Attributes:
        codigo (CharField): Código único de la terminal (ej. T01). Forma parte de su URL.
        nombre (CharField): Nombre descriptivo.
        ubicacion (CharField): Sucursal o dirección donde está instalada.
        activa (BooleanField): Una terminal inactiva no acepta operaciones.
    """
    codigo = models.CharField(max_length=20, unique=True, verbose_name="Código")
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    ubicacion = models.CharField(max_length=200, blank=True, default='', verbose_name="Ubicación")
    activa = models.BooleanField(default=True, verbose_name="Activa")

    class Meta:
        verbose_name = "Terminal de Autoservicio"
        verbose_name_plural = "Terminales de Autoservicio"
        ordering = ['codigo']

    def __str__(self):
        """Devuelve la representación en cadena de la terminal."""
        return f"{self.nombre} ({self.codigo})"


class StockBilleteTerminal(models.Model):
    """
    Cantidad de billetes de una denominación disponibles dentro de una terminal de autoservicio.

    Attributes:
        terminal (TerminalAutoservicio): Terminal a la que pertenece el stock.
        denominacion (DenominacionDivisa): Denominación del billete.
        cantidad (PositiveIntegerField): Billetes disponibles.
    """
    terminal = models.ForeignKey(TerminalAutoservicio, on_delete=models.CASCADE, related_name='stock', verbose_name="Terminal")
    denominacion = models.ForeignKey(DenominacionDivisa, on_delete=models.CASCADE, verbose_name="Denominación")
    cantidad = models.PositiveIntegerField(default=0, verbose_name="Cantidad de Billetes")

    class Meta:
        verbose_name = "Stock de Billetes de Terminal"
        verbose_name_plural = "Stock de Billetes de Terminales"
        unique_together = ('terminal', 'denominacion')
        ordering = ['denominacion__divisa', '-denominacion__valor_facial']

    def __str__(self):
        """Devuelve la representación en cadena del stock."""
        return f"{self.terminal.codigo}: {self.cantidad} x {self.denominacion.nombre}"


class OperacionTerminal(models.Model):
    """
    Operación en efectivo generada por el cliente desde la web y completada en una terminal de autoservicio.

    Tipos (mismo criterio que la ventanilla, desde el punto de vista de la casa de cambios):

    * ``COMPRA``: la casa compra divisas. El cliente deposita divisa extranjera y recibe Guaraníes.
    * ``VENTA``: la casa vende divisas. El cliente deposita Guaraníes y recibe divisa extranjera.

    Attributes:
        codigo (CharField): Código numérico de 8 dígitos que el cliente ingresa en la terminal.
        cliente (Cliente): Cliente que generó la operación.
        usuario (User): Usuario que la generó desde la web.
        terminal (TerminalAutoservicio): Terminal donde se completó (vacía mientras está pendiente).
        tipo_operacion (CharField): ``COMPRA`` o ``VENTA``.
        moneda_origen (CharField): Moneda que deposita el cliente.
        moneda_destino (CharField): Moneda que recibe el cliente.
        monto_origen (DecimalField): Monto a depositar (en billetes).
        monto_destino (DecimalField): Monto a entregar (en billetes).
        tasa_aplicada (DecimalField): Tasa de la cotización.
        tasa_estandar (DecimalField): Tasa sin beneficio por categoría.
        benefit_percentage (DecimalField): Beneficio por categoría aplicado.
        comision (DecimalField): Comisión de la operación (en la moneda de origen).
        valor_pyg (DecimalField): Valor de la operación en Guaraníes (auditoría).
        redondeo_pyg (DecimalField): Ajuste a billetes de Guaraníes (siempre menor al billete más chico).
        estado (CharField): PENDIENTE, COMPLETADA, CANCELADA o VENCIDA.
        expira_en (DateTimeField): Vencimiento del código.
        efectivo_depositado (DecimalField): Total depositado en la terminal.
        vuelto (DecimalField): Vuelto entregado en la moneda de origen.
        billetes_depositados (JSONField): Detalle {valor_facial: cantidad} de lo depositado.
        billetes_entregados (JSONField): Detalle {moneda: {valor_facial: cantidad}} de lo entregado.
        motivo_cancelacion (CharField): Motivo si se canceló.
        creado_en (DateTimeField): Fecha de generación.
        completado_en (DateTimeField): Fecha en que se completó en la terminal.
    """
    PENDIENTE = 'PENDIENTE'
    COMPLETADA = 'COMPLETADA'
    CANCELADA = 'CANCELADA'
    VENCIDA = 'VENCIDA'
    ESTADO_CHOICES = [
        (PENDIENTE, 'Pendiente de completar en terminal'),
        (COMPLETADA, 'Completada'),
        (CANCELADA, 'Cancelada'),
        (VENCIDA, 'Vencida'),
    ]
    TIPO_OPERACION_CHOICES = OperacionVentanilla.TIPO_OPERACION_CHOICES

    codigo = models.CharField(max_length=8, db_index=True, verbose_name="Código de Operación")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='operaciones_terminal', verbose_name="Cliente")
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    terminal = models.ForeignKey(TerminalAutoservicio, on_delete=models.SET_NULL, null=True, blank=True, related_name='operaciones', verbose_name="Terminal")
    tipo_operacion = models.CharField(max_length=20, choices=TIPO_OPERACION_CHOICES, verbose_name="Tipo de Operación")
    moneda_origen = models.CharField(max_length=10, verbose_name="Moneda a Depositar")
    moneda_destino = models.CharField(max_length=10, verbose_name="Moneda a Recibir")
    monto_origen = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Monto a Depositar")
    monto_destino = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Monto a Recibir")
    tasa_aplicada = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Aplicada")
    tasa_estandar = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Estándar")
    benefit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Beneficio por Categoría (%)")
    comision = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Comisión")
    valor_pyg = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Valor en PYG")
    redondeo_pyg = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="Redondeo a Billetes (PYG)")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=PENDIENTE, verbose_name="Estado")
    expira_en = models.DateTimeField(verbose_name="Vence")
    efectivo_depositado = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Efectivo Depositado")
    vuelto = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'), verbose_name="Vuelto")
    billetes_depositados = models.JSONField(default=dict, blank=True, verbose_name="Billetes Depositados")
    billetes_entregados = models.JSONField(default=dict, blank=True, verbose_name="Billetes Entregados")
    motivo_cancelacion = models.CharField(max_length=200, blank=True, default='', verbose_name="Motivo de Cancelación")
    creado_en = models.DateTimeField(auto_now_add=True, verbose_name="Generada")
    completado_en = models.DateTimeField(null=True, blank=True, verbose_name="Completada")

    class Meta:
        verbose_name = "Operación de Terminal de Autoservicio"
        verbose_name_plural = "Operaciones de Terminal de Autoservicio"
        ordering = ['-creado_en']

    def __str__(self):
        """Devuelve la representación en cadena de la operación."""
        return f"TAUser {self.codigo} - {self.tipo_operacion}: {self.monto_origen} {self.moneda_origen} -> {self.monto_destino} {self.moneda_destino} ({self.estado})"

    @property
    def divisa_extranjera(self):
        """Divisa extranjera de la operación (la que no es PYG)."""
        return self.moneda_origen if self.moneda_origen != 'PYG' else self.moneda_destino

    @property
    def tasa_final(self):
        """
        Guaraníes por unidad de divisa que efectivamente resultan de la operación (comisión incluida).

        Es la tasa que se muestra al cliente: la comisión va dentro del precio y no se informa aparte.
        """
        if self.moneda_origen == 'PYG':
            guaranies, divisa = self.monto_origen, self.monto_destino
        else:
            guaranies, divisa = self.monto_destino, self.monto_origen
        if not divisa:
            return self.tasa_aplicada
        return guaranies / divisa
