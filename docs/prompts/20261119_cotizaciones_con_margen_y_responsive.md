# Registro de Conversación IA - Cotizaciones con margen para clientes con beneficio y diseño responsive

- **Fecha:** 19/11/2026
- **Rama:** `develop`
- **Asistente:** Claude Code (Claude Opus 5.5)

## Pedido del usuario
> "cambiar el valor de las divisas también así como para al tener clientes con beneficio se tenga ganancias,
> [...] dólar vale 7150 guaraníes. También quiero que hagas que el sistema sea responsive [...] lo que está en
> la barra como cerrar sesión, cambiar entre clientes, usuario, todo eso se muestra encimado en un celular."

## 1. Cotizaciones
El beneficio se aplica sobre la tasa (compra × (1 + b), venta × (1 − b)). Con la pizarra anterior
(USD 7.300 / 7.450, spread ≈ 2 %) los clientes VIP (2 %) y Corporativo (4 %) dejaban margen negativo.

Se tomó ₲ 7.150 como valor de mercado (media) del dólar y compra/venta a ±4,5 % de la media, para que el
margen sea positivo incluso con el 4 % Corporativo:

| Divisa | Compra | Venta | Media | Margen por unidad (Minorista / VIP / Corporativo, peor lado) |
|---|---|---|---|---|
| USD | 6.830 | 7.470 | 7.150 | 320 / 170,60 / 21,20 |
| EUR | 7.665 | 8.385 | 8.025 | 360 / 192,30 / 24,60 |
| BRL | 1.335 | 1.465 | 1.400 | 65 / 35,70 / 6,40 |
| ARS | 7,85 | 8,65 | 8,25 | 0,40 / 0,23 / 0,05 |

- Nueva fuente única `tasas_cambio/divisas_default.py` (antes la lista estaba copiada en 9 lugares).
- Migración `tasas_cambio/0014_actualizar_cotizaciones_con_margen`: actualiza las divisas existentes y
  registra el cambio en `ExchangeRateHistory` (no crea divisas).
- `test_PSE_40` ajustado a los nuevos valores del seed.

## 2. Responsive
No hay plantilla base: cada página repite su barra. Se creó `authentication/_responsive.html` (CSS + JS) y se
incluye en el `<head>` de las 39 páginas completas:
- En pantallas < 768 px el grupo derecho de la barra se pliega en un menú con botón de hamburguesa; cada
  control (rol, cliente, modo usuario, usuario, cerrar sesión) ocupa el ancho completo.
- Las tablas sin contenedor con scroll se envuelven en uno con scroll horizontal.
- Se agregó `<meta name="viewport">` a las 4 páginas de caja que no lo tenían.

## 3. Operación con ganancia negativa en el remoto y acreditación en la caja del negocio
> "así me sale la operación [...] no quería que salga en negativo [...] y también quiero que atiendas que se
> deposite bien el monto en el método de pago del sistema."

**Análisis de la operación del dashboard:** volumen ₲7.242.000 = 1.000 USD × 7.100 × 1,02 → cliente VIP, compra
cruzada USD→EUR hecha con la pizarra anterior (USD compra 7.100). Con ese spread el 2 % VIP superaba el margen de
la pata EUR, por eso es negativa. No se reescribe una operación ya confirmada; las nuevas usan la pizarra de ±4,5 %.

**Errores corregidos en las operaciones cruzadas:**
- Compra online (`process_purchase`, `initiate_purchase`): `tipo_cambio_local_origen` y `total_pyg` usaban la tasa
  cruzada → la caja del negocio recibía menos de ₲1.000 por 1.000 USD (1.000 × 0,85). Ahora recibe el valor en PYG (monto × compra con beneficio).
- Venta online (`process_sale`, `initiate_sale`, `confirm_sale`): mismo error al descontar de la cuenta del negocio;
  además la cuenta destino del cliente recibía `total_pyg` reconvertido. Ahora, si la cuenta está en la moneda de
  destino, se acredita el monto convertido.
- Ventanilla (`caja/cotizacion.py`): la pata que la casa vende usaba tasa de compra. Ahora origen = compra y
  destino = venta (sin cambios para operaciones con PYG).
- Admin de Django: los cambios de tasa se registran en `ExchangeRateHistory` (el dashboard mide el margen con él).

Simulación de 1.000 USD→EUR con la pizarra nueva: ganancia neta ₲644.390 (Minorista), ₲377.754 (VIP), ₲100.705 (Corporativo).

## 4. Comisión online cobrada dentro de la tasa
> "el tema de la comisión agrégalo pero que el cliente no vea que lo cobro, ya tiene que estar dentro del precio
> de las divisas ese cobro."

Antes la comisión online se registraba pero no se cobraba. Ahora se cobra dentro de la tasa (`tasas_cambio/comision.py`):
cargo 0,55 % (comisión 0,5 % + IVA 10 %), el cliente recibe `convertido × 0,9945`.
- `SimuladorConversionService.simular(..., incluir_comision=True)`: la tasa y el monto mostrados ya incluyen el
  cargo. Se usa en el simulador del cliente, compra/venta online y sus confirmaciones. Las conversiones internas
  entre cuentas (`_convert_amount`) siguen sin cargo.
- Venta: la comisión se calcula sobre el monto sin cargo (`converted_amount_sin_comision`).
- Nuevo campo `comision_en_tasa` (migración `procesamiento_operaciones/0015`). Las pendientes creadas antes se
  confirman con el criterio anterior, para no cancelarlas por "variación de tasa".
- Reporte: en esas operaciones el margen se mide con la tasa sin cargo (pizarra con beneficio) y el IVA no se
  descuenta (lo pagó el cliente). Verificado: ganancia del reporte = lo que gana la casa a tasa media − IVA.
- Terminal de autoservicio: muestra la tasa final (comisión incluida) y ya no dice "Comisión incluida".
- Tests: `tasas_cambio/test_comision_en_tasa.py` y dos casos nuevos en `test_PSE_24`; ajustados PSE-11, PSE-14,
  PSE-31, simulador→operación y operaciones cruzadas.
