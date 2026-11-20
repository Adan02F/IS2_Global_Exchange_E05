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
