# Registro de Conversación IA - Terminal de autoservicio (TAUser) para operaciones en efectivo

- **Fecha:** 18/11/2026
- **Rama:** `develop`
- **Asistente:** Claude Code (Claude Opus 5.5)

## Pedido del usuario
> "Armarme el modo terminal sobre terminal autoservicio metalico como me lo dijiste. y dime como usarlo."

Contexto: el QA del Sprint 6 pide revisar el "manejo completo de transacciones (acreditación, cancelación)
incluyendo terminal de autoservicio (metálico)". Hasta ahora el efectivo solo se operaba en la ventanilla
atendida por un cajero (PSE-38).

## Diseño acordado
- **Terminal de autoservicio** = kiosco que usa el propio cliente, sin cajero, con billetes ("metálico").
- El cliente genera la operación en la web y recibe un **código de 8 dígitos** (vale 30 minutos).
- En la terminal ingresa **código + cédula/RUC** (segundo factor), deposita billetes y retira el dinero.
- La terminal tiene **stock propio de billetes por denominación**; el administrador lo recarga y retira.
- Mismo cálculo que la ventanilla (tasa con beneficio por categoría y comisión 0,5 %): se extrajo a
  `caja/cotizacion.py` y la ventanilla pasó a usarlo, sin cambiar su comportamiento.
- Los Guaraníes se ajustan al billete de 1.000 Gs a favor de la casa; la divisa debe poder formarse con billetes.
- Si la cotización cambió desde que se generó el código, la terminal pide aceptar la nueva o cancelar.
- La operación completada se registra como compra/venta con `payment_method_type = EFECTIVO_TERMINAL`:
  aparece en historial, reportes (canal "Terminal"), tablero de ganancias y se le emite la factura SIFEN.
  Para la anulación de factura se trata como operación en efectivo (igual que la ventanilla).

## Archivos
| Archivo | Cambio |
|---|---|
| `caja/models.py` | `TerminalAutoservicio`, `StockBilleteTerminal`, `OperacionTerminal` |
| `caja/migrations/0007_terminal_autoservicio.py` | Migración de los tres modelos |
| `caja/cotizacion.py` | Cotización en efectivo compartida (ventanilla y terminal) |
| `caja/terminal.py` | Servicio: desglose de billetes con stock limitado, cotización ajustada, ciclo de vida |
| `caja/views.py`, `caja/urls.py` | Vistas del cliente, del kiosco y del administrador; la ventanilla usa `cotizacion.py` |
| `caja/templates/caja/terminal_*.html`, `terminales_admin.html` | Pantallas |
| `caja/admin.py` | Registro en Django Admin con stock editable |
| `authentication/.../seed_data.py` | Crea la terminal T01 con 50 billetes de cada denominación |
| Dashboards (cliente, admin, accesos por permiso) | Tarjetas de acceso |
| `monitoreo_corporativo/reportes.py` | Canal "Terminal" y comisión de efectivo convertida a PYG |
| `procesamiento_operaciones/facturacion.py` | `es_operacion_de_ventanilla` incluye `EFECTIVO_TERMINAL` |
| `caja/test_terminal_autoservicio.py` | 35 pruebas |

## Cómo se usa
1. **Cliente** (`cliente` / `password123`): Panel → "Terminal de Autoservicio" → elegir *Vender divisas*
   o *Comprar divisas*, divisa y monto → "Generar código".
2. **Terminal**: abrir `/caja/terminal/T01/` (sin iniciar sesión; en otra pestaña o equipo) → código + documento
   → indicar los billetes depositados → "Confirmar y retirar dinero" → comprobante con los billetes entregados.
3. **Administrador**: Panel → "Terminales de Autoservicio" → crear terminales, activar/desactivar, recargar o
   retirar billetes y ver las últimas operaciones.

## Decisiones y observaciones
- Se descartó una migración que Django generó en `authentication` (convertía los `id` de 10 tablas de
  `BigAutoField` a `AutoField`). Es una diferencia previa entre modelos y migraciones, ajena a la terminal.
- Seguridad del kiosco: código aleatorio de 8 dígitos + documento, vencimiento de 30 minutos y bloqueo de la
  sesión 5 minutos tras 5 intentos fallidos.

## Verificación
- 35 pruebas nuevas y suite completa: 481 pruebas, todas aprobadas.
