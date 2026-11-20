# Registro de Conversación IA - Rol único: sin roles múltiples ni superposición con cliente

- **Fecha:** 19/11/2026
- **Rama:** `develop`
- **Asistente:** Claude Code (Claude Opus 5.5)

## Pedido del usuario
> "quiero que quites los roles múltiples, los roles principales no se pueden superponer, por ejemplo un usuario
> que está asociado a un cliente, y yo a ese usuario le asigno el rol de administrador no le tiene que salir eso
> de cliente ni ese apartado donde puede cambiar de cliente; eso solo le tiene que aparecer si no tiene rol y
> está asociado a un cliente, o que su rol sea cliente."

## Regla
Cada usuario tiene **un único rol** (`UserProfile.role`). La parte de cliente (cliente activo, selector de clientes
y grupos, panel de cliente, operar como cliente) solo existe si el usuario **no tiene rol o su rol es Cliente**.
Administrador, Analista, Cajero y superusuario nunca la ven, aunque estén asociados a un cliente.
La asociación usuario–cliente no se borra: si después se le quita el rol de personal, vuelve a ver su cliente.

## Cambios
- `authentication/permisos.py`: `es_rol_de_personal` y `puede_operar_como_cliente` (fuente única de la regla);
  `_rol_de_sistema` devuelve solo `profile.role` (sin rol activo elegido).
- `tasas_cambio/views.py`: `get_active_client` no devuelve cliente al personal (y limpia la sesión);
  `get_user_effective_category` → Minorista para el personal.
- `gestion_clientes/views.py`: `get_user_interface_context` devuelve una barra sin clientes, grupos ni roles para el
  personal; `switch_client`/`switch_group` lo ignoran; `switch_role` queda como redirección obsoleta; alta y edición
  de usuarios sin roles adicionales; aprobar una solicitud de cliente ya no agrega "Cliente" como rol extra.
- `authentication/views.py`: el panel de cliente solo para quien puede operar como cliente.
- `procesamiento_operaciones/views.py`: compra y venta no listan clientes al personal.
- Plantillas: sin selector "Rol Activo" (4 dashboards y lista de usuarios), sin "Roles Múltiples" en la lista ni
  "Roles Múltiples Adicionales" en el formulario de usuario.
- `UserProfile.roles` eliminado (migración `authentication/0025_quitar_roles_multiples`).
- Tests: `authentication/test_rol_unico.py`.
