# Registro de Conversación IA - PSE-24: Cálculo correcto de ganancias con divisas cruzadas y beneficios

- **Fecha:** 19/11/2026
- **Rama:** `develop`
- **Asistente:** Claude Code (Claude Opus 5.5)

## Pedido del usuario
> "dashboard de ganancias quiero que revises si está bien ya que compra y venta se hace de diferentes divisas
> [...] yo puedo comprar euro con dólar, no hace falta que sea sí o sí con guaraníes, y el cálculo para ese
> dashboard tiene que hacerlo de forma correcta atendiendo con qué moneda se está haciendo [...] y atender
> también los beneficios del cliente."

## Problemas encontrados (`monitoreo_corporativo/reportes.py`, compartido con PSE-41)
1. **Moneda de la comisión mal asumida.** `factor_comision_a_pyg` suponía que la comisión online estaba en la
   moneda de destino. Los servicios la guardan así:
   - Compra online (`CurrencyPurchaseService`): moneda de **origen**.
   - Venta online (`CurrencySaleService`): moneda de **destino**.
   - Efectivo (ventanilla y terminal): moneda de **origen**.

   Consecuencia (USD 7.300/7.450): compra PYG→USD de ₲745.000 → ₲27,7 M de comisión en vez de ₲3.725;
   compra USD→PYG y USD→EUR → ₲0,50 en vez de ₲3.650.
2. **`total_pyg` no está en PYG en las cruzadas.** El servicio lo calcula con la tasa cruzada (ej. 100 USD→EUR
   guarda `total_pyg = 89,57`). El volumen y el factor de conversión salían mal.
3. **No se consideraba el beneficio del cliente ni el margen cambiario.** El beneficio (VIP 2 %, Corporativo)
   se aplica sobre la tasa y reduce el margen, pero la ganancia era solo comisión − IVA.
4. **Los tests usaban datos irreales** (ej. comisión de una compra PYG→USD en USD, 1 USD → ₲2.000.000), por eso
   no detectaban los errores.

## Decisión del usuario
Ganancia neta = (comisión − IVA) + margen cambiario. El margen se mide contra la tasa media de pizarra del
momento de la operación (ya neto del beneficio), y el beneficio cedido se informa aparte.

## Solución
- `CotizacionesHistoricas`: pizarra vigente en cada momento desde `ExchangeRateHistory` (respaldo: `ExchangeRate`).
- `moneda_comision`, `tasas_por_pata`, `factor_comision_a_pyg`: comisión convertida con la tasa que se aplicó a
  esa moneda en la propia operación. En las cruzadas cada pata se reconstruye desde `monto_moneda_local`
  (en efectivo se descuenta o suma la comisión, que viene incluida en los montos).
- `margen_y_beneficio`: por pata, la casa compra (`cantidad × (media − tasa)`) o vende (`cantidad × (tasa − media)`).
- `volumen_pyg`: en las cruzadas usa el valor en PYG de la operación, no `total_pyg`.
- `FilaReporte`/`TotalesReporte`: `margen_cambiario_pyg`, `beneficio_pyg`, `ganancia_por_divisa`.
- Dashboard: el gráfico reparte la cruzada entre sus divisas; KPIs con margen y beneficios cedidos.
- Reporte financiero (web, impresión, CSV y PDF): columnas Margen Cambiario y Beneficio Cliente.
- Tests PSE-24 y PSE-41: fixtures con los valores que guardan los servicios y nueva clase
  `GananciaConDivisasCruzadasYBeneficioTests`.

## Observaciones fuera del alcance (no modificado)
- Con la pizarra por defecto (spread de ~2 %) un beneficio VIP de 2 % deja el margen cambiario **negativo**.
- La ventanilla cotiza la pata de destino de una cruzada con la tasa de **compra** (`tasa_con_beneficio` con
  `COMPRA`), y la casa la entrega por debajo de la media.
- El efectivo no registra IVA de la comisión (`tax_amount` queda en 0), a diferencia del canal online.
- El servicio online guarda `total_pyg` incorrecto en las cruzadas (el reporte ya no lo usa en ese caso).
