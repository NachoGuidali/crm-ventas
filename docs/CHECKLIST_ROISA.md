# Checklist de requerimientos (Roisa) — estado y plan

Fuente: "CHECKLIST DE REQUERIMIENTOS CRM – BAMINDS.docx" (06/10/2026). Leyenda: ✅ cumple · 🟡 parcial · ❌ falta.

| # | Requisito | Estado | Cómo se resuelve |
|---|---|---|---|
| 1.1 | Unificar duplicados + historial completo | ✅ | Deduplicación por teléfono, historial con autor |
| 1.2 | Cuántas veces ingresó y cómo se clasificó | 🟡→ bloque 1 | Contador "Ingresó N veces" en ficha y lista, filtro |
| 1.3 | Filtrar por fecha, fuente, estado, vendedora | ✅ | Lista de oportunidades |
| 2.1 | Plantillas enviadas por vendedora | 🟡→ bloque 1 | Reporte "Actividad por vendedora" |
| 2.2 | Número y duración de llamadas por vendedora | ✅ | Ranking |
| 2.3 | Mensajes/correos manuales y automáticos por vendedora | 🟡→ bloque 1 | Reporte "Actividad por vendedora" |
| 3.1 | Si el cliente respondió a los estímulos | 🟡→ bloque 4 | "Respondió" por envío; aperturas/clics de email |
| 3.2 | Calidad de mensajes automáticos según respuesta | ❌→ bloque 4 | % entregado / leído / respondido / avanzó por plantilla y automatización |
| 4.1 | Leads únicos en el período | 🟡→ bloque 1 | Únicos = nuevos + reingresos (contactos distintos) |
| 4.2 | Leads atendidos por vendedora | ✅ | Ranking |
| 4.3 | Leads no contactados dentro del SLA | ❌→ bloque 2 | SLA por embudo + reporte |
| 5.1 | Conversión etapa a etapa | 🟡→ bloque 1 | Embudo de conversión (cohorte) |
| 5.2 | Pipeline por vendedora y etapa | 🟡→ bloque 1 | Matriz vendedora × etapa |
| 6.1 | Panel de la vendedora con sus leads | ✅ | Mi día, tablero, lista |
| 6.2 | Llamar desde el CRM | ✅ | Anura Click2Dial |
| 6.3 | Plantillas o correos desde el panel | 🟡→ bloque 6 | WhatsApp ✅; email manual desde la ficha |
| 6.4 | Notas y cambios de estado desde el panel | ✅ | |
| 7.1 | Automatizaciones por tiempo desde la toma (día 3, día 7) | ✅ | Demora en la etapa inicial |
| 7.2 | Cambiar clasificación si no hay acción en X tiempo | ❌→ bloque 3 | "Sin actividad X días → mover / cerrar" |
| 7.3 | Automatizaciones por acción del cliente | ❌→ bloque 3 | "Respondió" / "No respondió en X h" |
| 7.4 | SLA y reasignación si no hay contacto | ❌→ bloque 2 | Reasignación automática + alerta |
| 8.1 | Formularios web / landings | ✅ | API |
| 8.2 | Correo, WhatsApp, SMS con trazabilidad | 🟡→ bloque 6 | WhatsApp ✅; email manual; SMS por Twilio |
| 8.3 | Telefonía con registro y duración | ✅ | Anura |
| 9.1 | Indicadores en tiempo real | 🟡→ bloques 1–2 | Refresco automático + "fuera de SLA" |
| 9.2 | Vistas por rol | 🟡→ bloque 1 | "Mis números" para la vendedora |
| 10.1 | Roles | ✅ | + roles a medida |
| 10.2 | Auditoría | ✅ | Historial con valor anterior → nuevo |
| 10.3 | Seguridad y privacidad | ✅ | (opcional: exportar/borrar datos de una persona) |
| 11.1 | Lead scoring | ❌→ bloque 7 | Puntaje por reglas |
| 11.2 | Informes de marketing / campañas | ✅ | Análisis de pautas |
| 11.3 | Informes predictivos | ❌→ bloque 7 | Proyección de ventas del mes |
| 11.4 | Pipelines visuales | ✅ | Tablero |
| 11.5 | Discador automático | ✅ | Progresivo |
| 34 | Tiempo de habla, logueo, after call work, cantidad de llamadas | 🟡→ bloque 5 | Sesiones de conexión + ACW |
| 35 | Discador integrado | ✅ | |

## Plan

1. ✅ **Reportes con datos existentes** (hecho 06/10): actividad por vendedora, embudo de conversión, matriz
   vendedora × etapa, leads únicos, contador de ingresos, "Mis números", refresco automático.
2. ✅ **SLA** (hecho 06/10): definición por embudo, reporte, reasignación automática, alerta.
3. ✅ **Automatizaciones por respuesta e inactividad** (hecho 06/10).
4. ✅ **Calidad de envíos** (hecho 06/10).
5. ✅ **Métricas de call center** (hecho 06/10): tiempo conectado, after call work.
6. **Email manual** y **SMS** (1,5–2,5 días).
7. **Lead scoring** y **proyección** (2 días, opcional).
