# -*- coding: utf-8 -*-
"""
Pruebas de la terminal de autoservicio (TAUser) para operaciones en efectivo ("metálico").

Valida:
* Desglose de montos en billetes con stock limitado.
* Cotización ajustada a billetes (redondeo de Guaraníes a favor de la casa) y montos no formables.
* Ciclo de vida: generación del código, búsqueda con documento, vencimiento, cancelación y cambio de cotización.
* Depósito: monto insuficiente, vuelto, falta de billetes (sin modificar el stock) y registro de la transacción.
* Integración con reportes financieros (canal Terminal) y facturación (operación en efectivo).
* Vistas: página del cliente, kiosco sin inicio de sesión (con bloqueo por intentos) y gestión del administrador.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from authentication.models import Cliente, Role, UserProfile, UsuarioClienteRelacion
from caja import terminal as tauser
from caja.models import (
    OperacionTerminal, StockBilleteTerminal, TerminalAutoservicio, inicializar_denominaciones_default,
)
from monitoreo_corporativo import reportes
from procesamiento_operaciones import facturacion
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate

D = Decimal


class BaseTerminal(TestCase):
    """Tasas, denominaciones, una terminal con 20 billetes de cada valor, un cliente minorista y su usuario."""

    def setUp(self):
        """Crea los datos comunes."""
        inicializar_denominaciones_default()
        self.usd = ExchangeRate.objects.create(currency_code='USD', currency_name='Dólar', buy_rate=D('7300'), sell_rate=D('7400'))
        ExchangeRate.objects.create(currency_code='EUR', currency_name='Euro', buy_rate=D('8000'), sell_rate=D('8100'))
        self.terminal = TerminalAutoservicio.objects.create(codigo='T99', nombre='Terminal de Prueba')
        tauser.asegurar_stock(self.terminal)
        StockBilleteTerminal.objects.filter(terminal=self.terminal).update(cantidad=20)

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Cliente Terminal', documento_identidad='5550001', email='t@x.com', categoria='MINORISTA',
        )
        self.usuario = User.objects.create_user(username='cliente_tauser', password='x')
        UserProfile.objects.create(user=self.usuario, role=Role.objects.get_or_create(name='Cliente')[0])
        UsuarioClienteRelacion.objects.create(keycloak_user_id=str(self.usuario.id), cliente=self.cliente)

    def stock(self, moneda, valor):
        """Cantidad de billetes de un valor en la terminal."""
        return StockBilleteTerminal.objects.get(
            terminal=self.terminal, denominacion__divisa=moneda, denominacion__valor_facial=D(valor),
        ).cantidad

    def generar(self, tipo='COMPRA', divisa='USD', monto='100'):
        """Genera una operación pendiente del cliente de prueba."""
        return tauser.generar_operacion(self.usuario, self.cliente, tipo, divisa, D(monto))


class DesgloseBilletesTests(TestCase):
    """Armado de montos con billetes."""

    def test_usa_primero_los_billetes_grandes(self):
        """Con stock suficiente, 180 = 100 + 50 + 20 + 10."""
        disponibles = {D(100): 5, D(50): 5, D(20): 5, D(10): 5}
        self.assertEqual(tauser.desglosar(D(180), disponibles), {D(100): 1, D(50): 1, D(20): 1, D(10): 1})

    def test_busca_otra_combinacion_si_faltan_billetes(self):
        """Sin billetes de 10, 60 se arma con 3 de 20 (el método voraz con 50 fallaría)."""
        disponibles = {D(50): 1, D(20): 3, D(10): 0}
        self.assertEqual(tauser.desglosar(D(60), disponibles), {D(20): 3})

    def test_monto_imposible_o_con_centavos(self):
        """Un monto que no se puede formar, o con centavos, devuelve None."""
        self.assertIsNone(tauser.desglosar(D(30), {D(50): 2, D(20): 1}))
        self.assertIsNone(tauser.desglosar(D('10.50'), {D(10): 5, D(1): 5}))

    def test_monto_cero(self):
        """Cero no requiere billetes."""
        self.assertEqual(tauser.desglosar(D(0), {D(10): 1}), {})


class CotizacionTerminalTests(BaseTerminal):
    """Cotización con montos ajustados a billetes."""

    def test_vender_divisas_redondea_guaranies_hacia_abajo(self):
        """100 USD a 7.300 con 0,5 % de comisión = 726.350 Gs; la terminal entrega 726.000 Gs."""
        cot = tauser.cotizar(self.cliente, 'COMPRA', 'USD', D('100'))
        self.assertEqual((cot.moneda_origen, cot.moneda_destino), ('USD', 'PYG'))
        self.assertEqual(cot.monto_origen, D('100.00'))
        self.assertEqual(cot.monto_destino, D('726000.00'))
        self.assertEqual(cot.redondeo_pyg, D('350.00'))
        self.assertEqual(cot.tasa, D('7300'))

    def test_comprar_divisas_redondea_guaranies_hacia_arriba(self):
        """Para recibir 100 USD el cliente deposita el monto en Guaraníes redondeado al billete de 1.000."""
        cot = tauser.cotizar(self.cliente, 'VENTA', 'USD', D('100'))
        self.assertEqual((cot.moneda_origen, cot.moneda_destino), ('PYG', 'USD'))
        self.assertEqual(cot.monto_destino, D('100.00'))
        self.assertEqual(cot.monto_origen % 1000, 0)
        self.assertTrue(D('0') <= cot.redondeo_pyg < D('1000'))

    def test_montos_no_formables_se_rechazan(self):
        """No se aceptan centavos ni montos que no se arman con billetes (EUR: billete mínimo 5)."""
        with self.assertRaisesMessage(tauser.TerminalError, 'billetes'):
            tauser.cotizar(self.cliente, 'COMPRA', 'USD', D('10.50'))
        with self.assertRaisesMessage(tauser.TerminalError, 'billetes'):
            tauser.cotizar(self.cliente, 'COMPRA', 'EUR', D('7'))

    def test_datos_invalidos(self):
        """PYG no es divisa de la terminal, el monto debe ser positivo y el tipo válido."""
        with self.assertRaises(tauser.TerminalError):
            tauser.cotizar(self.cliente, 'COMPRA', 'PYG', D('1000'))
        with self.assertRaises(tauser.TerminalError):
            tauser.cotizar(self.cliente, 'COMPRA', 'USD', D('0'))
        with self.assertRaises(tauser.TerminalError):
            tauser.cotizar(self.cliente, 'OTRO', 'USD', D('10'))


class CicloDeVidaTests(BaseTerminal):
    """Generación, búsqueda, vencimiento, cancelación y cambio de cotización."""

    def test_genera_codigo_de_8_digitos_pendiente(self):
        """La operación queda pendiente, con código numérico y vencimiento futuro."""
        op = self.generar()
        self.assertRegex(op.codigo, r'^\d{8}$')
        self.assertEqual(op.estado, OperacionTerminal.PENDIENTE)
        self.assertGreater(op.expira_en, timezone.now())

    def test_sin_cliente_no_genera(self):
        """Sin cliente activo no se puede generar el código."""
        with self.assertRaisesMessage(tauser.TerminalError, 'cliente activo'):
            tauser.generar_operacion(self.usuario, None, 'COMPRA', 'USD', D('100'))

    def test_busqueda_exige_el_documento_correcto(self):
        """El código solo sirve junto con el documento del cliente."""
        op = self.generar()
        self.assertEqual(tauser.buscar_operacion(op.codigo, '5550001').pk, op.pk)
        with self.assertRaisesMessage(tauser.TerminalError, 'incorrectos'):
            tauser.buscar_operacion(op.codigo, '9999999')
        with self.assertRaisesMessage(tauser.TerminalError, 'incorrectos'):
            tauser.buscar_operacion('00000000', '5550001')

    def test_codigo_vencido(self):
        """Un código vencido se marca VENCIDA y no se puede usar."""
        op = self.generar()
        OperacionTerminal.objects.filter(pk=op.pk).update(expira_en=timezone.now() - timedelta(minutes=1))
        with self.assertRaisesMessage(tauser.TerminalError, 'venció'):
            tauser.buscar_operacion(op.codigo, '5550001')
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionTerminal.VENCIDA)

    def test_cancelacion(self):
        """Una operación cancelada ya no puede completarse ni volver a cancelarse."""
        op = self.generar()
        tauser.cancelar_operacion(op, 'Prueba')
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionTerminal.CANCELADA)
        with self.assertRaises(tauser.TerminalError):
            tauser.cancelar_operacion(op, 'Otra vez')
        with self.assertRaisesMessage(tauser.TerminalError, 'cancelada'):
            tauser.buscar_operacion(op.codigo, '5550001')

    def test_cambio_de_cotizacion(self):
        """Si la tasa cambia, la terminal detecta la diferencia y el cliente puede aceptar la nueva cotización."""
        op = self.generar()
        self.assertIsNone(tauser.cotizacion_cambio(op))
        self.usd.buy_rate = D('7000')
        self.usd.save()
        nueva = tauser.cotizacion_cambio(op)
        self.assertIsNotNone(nueva)
        self.assertLess(nueva.monto_destino, op.monto_destino)
        tauser.actualizar_cotizacion(op)
        op.refresh_from_db()
        self.assertEqual(op.monto_destino, nueva.monto_destino)
        self.assertEqual(op.tasa_aplicada, D('7000'))


class DepositoYEntregaTests(BaseTerminal):
    """Depósito de billetes, entrega, vuelto y registro de la transacción."""

    def test_vender_dolares_con_monto_exacto(self):
        """El cliente deposita 100 USD y recibe 726.000 Gs; el stock y la transacción se actualizan."""
        op = self.generar('COMPRA', 'USD', '100')
        op, tx = tauser.completar_operacion(op, self.terminal, {'100': 1})
        self.assertEqual(op.estado, OperacionTerminal.COMPLETADA)
        self.assertEqual(op.terminal, self.terminal)
        self.assertEqual(op.vuelto, D('0'))
        self.assertEqual(self.stock('USD', 100), 21)
        # 726.000 = 7 x 100.000 + 20.000 + 5.000 + 1.000
        self.assertEqual(op.billetes_entregados, {'PYG': {'100000': 7, '20000': 1, '5000': 1, '1000': 1}})
        self.assertEqual(self.stock('PYG', 100000), 13)
        self.assertIsInstance(tx, CurrencyPurchaseTransaction)
        self.assertEqual(tx.payment_method_type, tauser.PAGO_TERMINAL)
        self.assertEqual(tx.status, 'SUCCESS')
        self.assertEqual(tx.converted_amount, D('726000.00'))
        self.assertEqual(tx.total_pyg, D('726000.00'))
        self.assertIn(op.codigo, tx.transparent_breakdown)

    def test_comprar_dolares_con_vuelto(self):
        """El cliente deposita de más en Guaraníes y recibe los dólares más el vuelto."""
        op = self.generar('VENTA', 'USD', '50')
        a_pagar = op.monto_origen
        billetes_100k = int(a_pagar // 100000) + 1
        op, tx = tauser.completar_operacion(op, self.terminal, {'100000': billetes_100k})
        self.assertIsInstance(tx, CurrencySaleTransaction)
        self.assertEqual(op.vuelto, D(billetes_100k * 100000) - a_pagar)
        self.assertEqual(op.billetes_entregados['USD'], {'50': 1})
        self.assertIn('vuelto_PYG', op.billetes_entregados)
        self.assertEqual(self.stock('USD', 50), 19)

    def test_deposito_insuficiente(self):
        """Si no alcanza el depósito, la operación sigue pendiente y el stock no cambia."""
        op = self.generar('COMPRA', 'USD', '100')
        with self.assertRaisesMessage(tauser.TerminalError, 'debe depositar'):
            tauser.completar_operacion(op, self.terminal, {'50': 1})
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionTerminal.PENDIENTE)
        self.assertEqual(self.stock('USD', 50), 20)

    def test_sin_billetes_para_entregar_no_modifica_el_stock(self):
        """Sin Guaraníes en la terminal se rechaza la operación y los billetes depositados no quedan en el stock."""
        StockBilleteTerminal.objects.filter(terminal=self.terminal, denominacion__divisa='PYG').update(cantidad=0)
        op = self.generar('COMPRA', 'USD', '100')
        with self.assertRaisesMessage(tauser.TerminalError, 'no tiene billetes'):
            tauser.completar_operacion(op, self.terminal, {'100': 1})
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionTerminal.PENDIENTE)
        self.assertEqual(self.stock('USD', 100), 20)
        self.assertFalse(CurrencyPurchaseTransaction.objects.exists())

    def test_billetes_de_otra_moneda_o_negativos(self):
        """Solo se aceptan billetes de la moneda a depositar y cantidades positivas."""
        op = self.generar('COMPRA', 'USD', '100')
        with self.assertRaisesMessage(tauser.TerminalError, 'no acepta'):
            tauser.completar_operacion(op, self.terminal, {'100000': 1})
        with self.assertRaisesMessage(tauser.TerminalError, 'negativa'):
            tauser.completar_operacion(op, self.terminal, {'100': -1})

    def test_terminal_inactiva(self):
        """Una terminal fuera de servicio no completa operaciones."""
        self.terminal.activa = False
        self.terminal.save()
        with self.assertRaisesMessage(tauser.TerminalError, 'fuera de servicio'):
            tauser.completar_operacion(self.generar(), self.terminal, {'100': 1})

    def test_no_se_completa_dos_veces(self):
        """Una operación completada no puede volver a usarse."""
        op = self.generar('COMPRA', 'USD', '100')
        tauser.completar_operacion(op, self.terminal, {'100': 1})
        with self.assertRaisesMessage(tauser.TerminalError, 'completada'):
            tauser.completar_operacion(op, self.terminal, {'100': 1})

    def test_actualiza_volumen_del_cliente(self):
        """El volumen operado del cliente suma el valor en Guaraníes de la operación."""
        op = self.generar('COMPRA', 'USD', '100')
        tauser.completar_operacion(op, self.terminal, {'100': 1})
        self.cliente.refresh_from_db()
        self.assertEqual(D(str(self.cliente.transaction_volume)), D('726000.00'))


class IntegracionTests(BaseTerminal):
    """Reportes financieros y facturación."""

    def test_reporte_muestra_canal_terminal_y_comision_en_pyg(self):
        """La operación aparece en el reporte como canal Terminal, con la comisión convertida a PYG."""
        op = self.generar('COMPRA', 'USD', '100')
        tauser.completar_operacion(op, self.terminal, {'100': 1})
        filas, totales = reportes.generar_reporte(reportes.FiltrosReporte())
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0].canal, 'Terminal')
        self.assertEqual(filas[0].comision_pyg, D('3650.00'))  # 0,50 USD x 7.300
        self.assertEqual(totales.volumen_total_pyg, D('726000.00'))

    def test_facturacion_la_trata_como_operacion_en_efectivo(self):
        """La anulación de su factura no devuelve saldos en cuentas (se resuelve en la sucursal)."""
        op = self.generar('COMPRA', 'USD', '100')
        _, tx = tauser.completar_operacion(op, self.terminal, {'100': 1})
        self.assertTrue(facturacion.es_operacion_de_ventanilla(tx))

    def test_recarga_y_retiro_de_stock(self):
        """El administrador carga y retira billetes; no puede retirar más de lo que hay."""
        total = tauser.recargar_stock(self.terminal, 'USD', {'100': 5})
        self.assertEqual(total, D('500'))
        self.assertEqual(self.stock('USD', 100), 25)
        tauser.recargar_stock(self.terminal, 'USD', {'100': 25}, retirar=True)
        self.assertEqual(self.stock('USD', 100), 0)
        with self.assertRaisesMessage(tauser.TerminalError, 'Solo hay'):
            tauser.recargar_stock(self.terminal, 'USD', {'100': 1}, retirar=True)

    def test_terminal_demo_con_stock_inicial(self):
        """La carga inicial crea la terminal T01 con 50 billetes de cada valor, una sola vez."""
        terminal = tauser.crear_terminal_demo()
        self.assertEqual(terminal.codigo, 'T01')
        fila = StockBilleteTerminal.objects.filter(terminal=terminal).first()
        self.assertEqual(fila.cantidad, 50)
        StockBilleteTerminal.objects.filter(terminal=terminal).update(cantidad=3)
        tauser.crear_terminal_demo()
        fila.refresh_from_db()
        self.assertEqual(fila.cantidad, 3)


class VistasTerminalTests(BaseTerminal):
    """Página del cliente, kiosco y gestión del administrador."""

    def setUp(self):
        """Cliente HTTP con la sesión del cliente activo."""
        super().setUp()
        self.http = Client()
        self.url_cliente = reverse('caja:terminal_cliente')
        self.url_kiosco = reverse('caja:terminal_kiosco', args=[self.terminal.codigo])
        self.url_operacion = reverse('caja:terminal_kiosco_operacion', args=[self.terminal.codigo])

    def login_cliente(self):
        """Inicia sesión como el cliente con su cliente activo."""
        self.http.force_login(self.usuario)
        sesion = self.http.session
        sesion['active_client_id'] = str(self.cliente.id)
        sesion.save()

    def test_cliente_genera_y_cancela_codigo(self):
        """Desde la web el cliente genera el código y puede cancelarlo."""
        self.login_cliente()
        self.assertEqual(self.http.get(self.url_cliente).status_code, 200)
        respuesta = self.http.post(self.url_cliente, {'accion': 'generar', 'tipo_operacion': 'COMPRA', 'divisa': 'USD', 'monto': '100'}, follow=True)
        op = OperacionTerminal.objects.get()
        self.assertContains(respuesta, op.codigo)
        self.http.post(self.url_cliente, {'accion': 'cancelar', 'operacion_id': op.pk})
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionTerminal.CANCELADA)

    def test_cliente_ve_error_de_monto(self):
        """Un monto no formable muestra el error y no crea la operación."""
        self.login_cliente()
        respuesta = self.http.post(self.url_cliente, {'accion': 'generar', 'tipo_operacion': 'COMPRA', 'divisa': 'USD', 'monto': '10.5'}, follow=True)
        self.assertContains(respuesta, 'billetes')
        self.assertFalse(OperacionTerminal.objects.exists())

    def test_pagina_del_cliente_requiere_inicio_de_sesion(self):
        """Sin sesión se redirige al inicio de sesión."""
        self.assertEqual(self.http.get(self.url_cliente).status_code, 302)

    def test_kiosco_flujo_completo(self):
        """En la terminal, sin iniciar sesión: código + documento, depósito y comprobante."""
        op = self.generar('COMPRA', 'USD', '100')
        self.assertContains(self.http.get(self.url_kiosco), 'Código de operación')
        respuesta = self.http.post(self.url_kiosco, {'codigo': op.codigo, 'documento': '5550001'})
        self.assertRedirects(respuesta, self.url_operacion)
        pantalla = self.http.get(self.url_operacion)
        self.assertContains(pantalla, 'Inserte sus billetes de USD')
        self.assertContains(pantalla, 'name="billete_100.00"')
        final = self.http.post(self.url_operacion, {'accion': 'depositar', 'billete_100.00': '1'})
        self.assertContains(final, 'Operación completada')
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionTerminal.COMPLETADA)
        # La sesión de la terminal queda limpia para el siguiente cliente
        self.assertRedirects(self.http.get(self.url_operacion), self.url_kiosco)

    def test_kiosco_cancelacion(self):
        """El cliente puede cancelar la operación desde la terminal."""
        op = self.generar()
        self.http.post(self.url_kiosco, {'codigo': op.codigo, 'documento': '5550001'})
        respuesta = self.http.post(self.url_operacion, {'accion': 'cancelar'})
        self.assertContains(respuesta, 'Operación cancelada')
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionTerminal.CANCELADA)

    def test_kiosco_cambio_de_cotizacion(self):
        """Si la tasa cambió, la terminal no deja depositar hasta aceptar la nueva cotización."""
        op = self.generar()
        self.http.post(self.url_kiosco, {'codigo': op.codigo, 'documento': '5550001'})
        self.usd.buy_rate = D('7000')
        self.usd.save()
        pantalla = self.http.get(self.url_operacion)
        self.assertContains(pantalla, 'La cotización cambió')
        rechazo = self.http.post(self.url_operacion, {'accion': 'depositar', 'billete_100.00': '1'})
        self.assertContains(rechazo, 'Acepte la nueva cotización')
        self.http.post(self.url_operacion, {'accion': 'aceptar_cotizacion'})
        self.assertNotContains(self.http.get(self.url_operacion), 'La cotización cambió')

    def test_kiosco_bloquea_tras_intentos_fallidos(self):
        """Tras 5 códigos incorrectos la terminal bloquea la sesión, aun con un código válido."""
        op = self.generar()
        for _ in range(tauser_vistas_max_intentos()):
            self.http.post(self.url_kiosco, {'codigo': '00000000', 'documento': '5550001'})
        respuesta = self.http.post(self.url_kiosco, {'codigo': op.codigo, 'documento': '5550001'})
        self.assertContains(respuesta, 'Demasiados intentos')

    def test_kiosco_terminal_inactiva(self):
        """Una terminal desactivada muestra 'Fuera de servicio'."""
        self.terminal.activa = False
        self.terminal.save()
        self.assertContains(self.http.get(self.url_kiosco), 'Fuera de servicio')

    def test_gestion_admin(self):
        """El administrador ve las terminales y recarga billetes; el cliente no accede."""
        admin = User.objects.create_superuser(username='admin_tauser', password='x')
        UserProfile.objects.create(user=admin, role=Role.objects.get_or_create(name='Admin')[0])
        url = reverse('caja:terminales_admin')
        self.login_cliente()
        self.assertEqual(self.http.get(url).status_code, 302)
        self.http.force_login(admin)
        self.assertContains(self.http.get(url), 'Terminal de Prueba')
        self.http.post(url, {'accion': 'recargar', 'terminal_id': self.terminal.pk, 'moneda': 'USD', 'billete_100.00': '10'})
        self.assertEqual(self.stock('USD', 100), 30)
        self.http.post(url, {'accion': 'crear', 'codigo': 't02', 'nombre': 'Terminal Nueva'})
        self.assertTrue(TerminalAutoservicio.objects.filter(codigo='T02').exists())


def tauser_vistas_max_intentos():
    """Cantidad de intentos fallidos que bloquean la terminal."""
    from caja.views import MAX_INTENTOS_TERMINAL
    return MAX_INTENTOS_TERMINAL
