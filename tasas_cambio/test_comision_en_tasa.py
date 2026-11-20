# -*- coding: utf-8 -*-
"""
Pruebas de la comisión online cobrada dentro de la tasa (``tasas_cambio.comision``).

El cliente ve una sola tasa que ya incluye la comisión del 0,5 % y su IVA (0,55 % en total); las
conversiones internas (sin ``incluir_comision``) siguen siendo la conversión pura.
"""
from decimal import Decimal

from django.test import TestCase

from tasas_cambio.comision import CARGO_EN_TASA, FACTOR_NETO
from tasas_cambio.models import ExchangeRate
from tasas_cambio.views import SimuladorConversionService


class ComisionEnTasaTests(TestCase):
    """Tasa y monto convertido con la comisión incluida, para cada tipo de operación."""

    def setUp(self):
        """Pizarra USD 6.830 / 7.470 y EUR 7.665 / 8.385."""
        ExchangeRate.objects.create(currency_code='USD', currency_name='Dólar', buy_rate=Decimal('6830'), sell_rate=Decimal('7470'))
        ExchangeRate.objects.create(currency_code='EUR', currency_name='Euro', buy_rate=Decimal('7665'), sell_rate=Decimal('8385'))

    def test_constantes(self):
        """El cargo es comisión 0,5 % + IVA 10 % de la comisión."""
        self.assertEqual(CARGO_EN_TASA, Decimal('0.0055'))
        self.assertEqual(FACTOR_NETO, Decimal('0.9945'))

    def test_sin_comision_es_la_conversion_pura(self):
        """Por defecto (conversiones internas) no se agrega ningún cargo."""
        res = SimuladorConversionService.simular('PYG', 'USD', Decimal('747000'))
        self.assertEqual(res['applied_rate'], Decimal('7470.0000'))
        self.assertEqual(res['converted_amount'], Decimal('100.00'))
        self.assertFalse(res['comision_incluida'])

    def test_compra_paga_mas_guaranies_por_unidad(self):
        """PYG→USD: tasa 7.470 / 0,9945 = 7.511,3122; recibe 99,45 USD en vez de 100."""
        res = SimuladorConversionService.simular('PYG', 'USD', Decimal('747000'), incluir_comision=True)
        self.assertEqual(res['applied_rate'], Decimal('7511.3122'))
        self.assertEqual(res['converted_amount'], Decimal('99.45'))
        self.assertEqual(res['converted_amount_sin_comision'], Decimal('100.00'))
        self.assertTrue(res['comision_incluida'])

    def test_venta_recibe_menos_guaranies_por_unidad(self):
        """USD→PYG: tasa 6.830 × 0,9945 = 6.792,4350; 100 USD → ₲679.243,50."""
        res = SimuladorConversionService.simular('USD', 'PYG', Decimal('100'), incluir_comision=True)
        self.assertEqual(res['applied_rate'], Decimal('6792.4350'))
        self.assertEqual(res['converted_amount'], Decimal('679243.50'))
        self.assertEqual(res['converted_amount_sin_comision'], Decimal('683000.00'))

    def test_cruzada_aplica_el_cargo_una_sola_vez(self):
        """USD→EUR: 1.000 × 6.830 × 0,9945 / 8.385 = 810,07 EUR (sin cargo: 814,55)."""
        res = SimuladorConversionService.simular('USD', 'EUR', Decimal('1000'), incluir_comision=True)
        self.assertEqual(res['converted_amount'], Decimal('810.07'))
        self.assertEqual(res['converted_amount_sin_comision'], Decimal('814.55'))
        self.assertEqual(res['applied_rate'], Decimal('0.8101'))
