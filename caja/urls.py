# -*- coding: utf-8 -*-
from django.urls import path
from . import views

app_name = 'caja'

urlpatterns = [
    path('gestion/', views.gestion_caja_view, name='gestion_caja'),
    path('admin-gestion/', views.gestion_cajas_admin_view, name='gestion_cajas_admin'),
    path('abrir/<int:caja_id>/', views.abrir_turno_view, name='abrir_turno'),
    path('cerrar/<int:turno_id>/', views.cerrar_turno_view, name='cerrar_turno'),
    path('movimiento/<int:turno_id>/', views.registrar_movimiento_efectivo_view, name='movimiento_efectivo'),
    path('turno/<int:turno_id>/operaciones/', views.detalle_turno_operaciones_view, name='detalle_turno_operaciones'),
    path('ventanilla/', views.ventanilla_pos_view, name='ventanilla_pos'),
    path('ventanilla/procesar/', views.procesar_ventanilla_view, name='procesar_ventanilla'),
    path('ventanilla/solicitar-anulacion/', views.solicitar_anulacion_factura_caja_view, name='solicitar_anulacion_factura'),
    # Terminal de autoservicio (TAUser)
    path('terminal/mis-operaciones/', views.terminal_cliente_view, name='terminal_cliente'),
    path('terminal/gestion/', views.terminales_admin_view, name='terminales_admin'),
    path('terminal/<str:codigo_terminal>/', views.terminal_kiosco_view, name='terminal_kiosco'),
    path('terminal/<str:codigo_terminal>/operacion/', views.terminal_kiosco_operacion_view, name='terminal_kiosco_operacion'),
]
