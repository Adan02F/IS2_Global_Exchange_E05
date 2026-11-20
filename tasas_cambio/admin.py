# -*- coding: utf-8 -*-
from django.contrib import admin
from django.utils import timezone

from .models import ExchangeRate, ExchangeRateHistory

@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    """
    Panel de administración para las tasas de cambio de Global Exchange.
    """
    list_display = ('currency_code', 'currency_name', 'buy_rate', 'sell_rate', 'last_updated')
    search_fields = ('currency_code', 'currency_name')

    def save_model(self, request, obj, form, change):
        """
        Guarda la tasa y, si cambió la compra o la venta, la registra en el historial, igual que el
        panel de cotizaciones. El dashboard de ganancias usa ese historial para medir el margen.
        """
        super().save_model(request, obj, form, change)
        if not change or {'buy_rate', 'sell_rate'} & set(form.changed_data):
            ExchangeRateHistory.objects.create(
                currency_code=obj.currency_code, buy_rate=obj.buy_rate, sell_rate=obj.sell_rate,
                timestamp=timezone.now(),
            )
