# -*- coding: utf-8 -*-
"""
Rol único por usuario: los roles del sistema no se superponen.

* Un usuario tiene un solo rol principal (no hay roles adicionales ni selector de rol activo).
* La parte de cliente (cliente activo, selector de clientes, operaciones como cliente) solo aparece si el
  usuario no tiene rol o su rol es Cliente, y está asociado a un cliente.
* Un Administrador, Analista o Cajero asociado a un cliente no ve nada de ese cliente.
"""
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from authentication.models import Cliente, Role, UserProfile, UsuarioClienteRelacion
from authentication.permisos import puede_operar_como_cliente
from gestion_clientes.views import get_user_interface_context
from tasas_cambio.views import get_active_client


class RolUnicoTests(TestCase):
    """Un cliente asociado a usuarios con distintos roles."""

    def setUp(self):
        """Crea un cliente y un usuario asociado a él por cada tipo de rol."""
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Cliente Rol Único SA', documento_identidad='8001234-5',
            email='rolunico@test.com', categoria='VIP',
        )
        self.usuarios = {}
        for nombre in ('Admin', 'Analista', 'Cajero', 'Cliente', None):
            username = f"rol_{(nombre or 'sin_rol').lower()}"
            user = User.objects.create_user(username=username, password='password123')
            role = Role.objects.get_or_create(name=nombre)[0] if nombre else None
            UserProfile.objects.create(user=user, role=role)
            UsuarioClienteRelacion.objects.create(keycloak_user_id=str(user.id), cliente=self.cliente, rol_en_cliente='CLIENTE')
            self.usuarios[nombre] = user

    def _request(self, user):
        """Solicitud con sesión que ya tenía el cliente como activo."""
        request = RequestFactory().get('/')
        request.user = user
        request.session = {'active_client_id': str(self.cliente.id), 'active_role_id': 999}
        return request

    def test_quien_puede_operar_como_cliente(self):
        """Solo sin rol o con rol Cliente; nunca el personal ni un superusuario."""
        self.assertTrue(puede_operar_como_cliente(self.usuarios[None]))
        self.assertTrue(puede_operar_como_cliente(self.usuarios['Cliente']))
        for rol in ('Admin', 'Analista', 'Cajero'):
            with self.subTest(rol=rol):
                self.assertFalse(puede_operar_como_cliente(self.usuarios[rol]))
        superusuario = User.objects.create_superuser(username='super_rol', password='x')
        self.assertFalse(puede_operar_como_cliente(superusuario))

    def test_personal_no_tiene_cliente_activo(self):
        """Aunque la sesión traiga un cliente, el personal no opera para él (y se limpia la sesión)."""
        for rol in ('Admin', 'Analista', 'Cajero'):
            with self.subTest(rol=rol):
                request = self._request(self.usuarios[rol])
                self.assertIsNone(get_active_client(self.usuarios[rol], request))
                self.assertNotIn('active_client_id', request.session)

    def test_cliente_y_usuario_sin_rol_tienen_cliente_activo(self):
        """Con rol Cliente o sin rol, el cliente asociado es el activo."""
        for rol in ('Cliente', None):
            with self.subTest(rol=rol):
                self.assertEqual(get_active_client(self.usuarios[rol], self._request(self.usuarios[rol])), self.cliente)

    def test_barra_del_personal_sin_selector_de_cliente_ni_de_rol(self):
        """El contexto de la barra del personal no trae clientes, grupos ni roles para elegir."""
        for rol in ('Admin', 'Analista', 'Cajero'):
            with self.subTest(rol=rol):
                user = self.usuarios[rol]
                ctx = get_user_interface_context(self._request(user), user.profile)
                self.assertEqual(ctx['clientes_asociados'], [])
                self.assertIsNone(ctx['active_client'])
                self.assertEqual(ctx['corporate_groups'], [])
                self.assertEqual(ctx['user_roles'], [])
                self.assertEqual(ctx['active_role'], user.profile.role)

    def test_barra_del_cliente_con_selector_de_cliente(self):
        """El usuario con rol Cliente sí ve su cliente, pero tampoco tiene selector de rol."""
        user = self.usuarios['Cliente']
        ctx = get_user_interface_context(self._request(user), user.profile)
        self.assertEqual(ctx['clientes_asociados'], [self.cliente])
        self.assertEqual(ctx['active_client'], self.cliente)
        self.assertEqual(ctx['user_roles'], [])

    def test_admin_asociado_a_cliente_ve_su_panel_sin_parte_de_cliente(self):
        """El dashboard del Administrador no muestra el selector ni el nombre del cliente."""
        self.client.force_login(self.usuarios['Admin'])
        respuesta = self.client.get(reverse('dashboard_redirect'))
        self.assertTemplateUsed(respuesta, 'authentication/admin_dashboard.html')
        self.assertNotContains(respuesta, 'Cliente Rol Único SA')
        self.assertNotContains(respuesta, 'Rol Activo')

    def test_cliente_ve_su_panel_de_cliente(self):
        """El usuario con rol Cliente entra a su panel con el cliente asociado."""
        self.client.force_login(self.usuarios['Cliente'])
        respuesta = self.client.get(reverse('dashboard_redirect'))
        self.assertTemplateUsed(respuesta, 'authentication/client_dashboard.html')
        self.assertContains(respuesta, 'Cliente Rol Único SA')

    def test_admin_no_puede_cambiar_a_un_cliente(self):
        """El cambio de cliente activo se ignora para el personal."""
        self.client.force_login(self.usuarios['Admin'])
        self.client.get(reverse('switch_client', args=[self.cliente.id]))
        self.assertNotIn('active_client_id', self.client.session)
