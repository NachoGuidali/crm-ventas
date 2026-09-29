# Integración con Anura (telefonía en la nube)

Guía de referencia para conectar un CRM con Anura. Está escrita a partir de la documentación pública de Anura
y de lo que confirmó su soporte (septiembre 2026). Sirve para este proyecto y como base para otros.

> **Estado:** probado contra Anura real (29/09/2026): Click2Dial + eventos START/TALK/END funcionando (en curso,
> atendida con duración, fin). Pendiente: probar entrantes reales y grabaciones.

---

## 1. Qué permite Anura y qué no

| Necesidad | ¿Se puede? | Cómo |
|---|---|---|
| Originar una llamada desde el CRM | Sí | API **Click2Dial** (token Bearer) |
| Cortar una llamada en curso por API | **No** | El "Tenant CallManager" no está disponible. El agente corta desde su teléfono. |
| Enterarse de las llamadas (entrantes y salientes) | Sí | **WebHooks templetizados** (Eventos) que Anura envía al CRM |
| Grabaciones | Sí | Link de descarga en la variable `{{ audio_file_mp3 }}` del evento de fin |
| Troncal SIP | No hace falta | Anura sigue siendo la central; la integración es por API + eventos |
| Discado predictivo | No | Solo Click2Dial de a una llamada → se implementa **discador progresivo** |
| API de tenant (CDRs, números bloqueados) | Sin confirmar | Opcional en el CRM; apagada por defecto |

Anura confirmó por soporte: *"sobre uso de eventos y API en tu CRM, no por medio de troncal SIP, podés usarlos sin
inconveniente"* y *"el endpoint de Tenant CallManager no está disponible, no es posible gestionar la llamada en curso
a nivel API"*.

---

## 2. Qué pedirle al cliente (administrador de su cuenta de Anura)

1. **Token de Click2Dial**: se genera en el panel de Anura → *Integraciones → Click 2 Dial* (o se pide a soporte:
   011-5263-0000 opción 3, soporte@anura.com.ar, chat del panel).
2. **Extensión de cada agente** (y, si difiere, su usuario / nombre de cuenta en Anura).
3. **Que cada agente tenga una terminal principal registrada** (softphone web, de escritorio, app o teléfono IP).
4. **Números (DID) y colas** por los que entran las llamadas, y a qué campaña/embudo va cada uno.
5. **Dirección del Anura web** que usan los agentes (para el botón "Ir a Anura").
6. Acceso de alguien con rol de configuración para cargar los **Eventos** (o que los carguen ellos con esta guía).

---

## 3. Originar llamadas: API Click2Dial

Documentación: <https://kb.anura.com.ar/es/articles/3250664-api-click-to-dial-click2dial>

```
POST https://api.anura.com.ar/adapter/default/click2call      (Perú: https://api.anura.pe/adapter/default/click2call)
Authorization: Bearer <TOKEN_CLICK2DIAL>
Content-Type: application/x-www-form-urlencoded

called=5491155554444&extension=201&customs=crm-1234
```

| Campo | Qué es |
|---|---|
| `called` | Número a llamar (sin `+`) |
| `extension` | Extensión del agente que origina |
| `customs` | Hasta 6 valores (se repite el campo). Vuelven en los eventos como `{{ custom1 }}` … `{{ custom6 }}` |

**Flujo:** Anura hace sonar la **terminal principal** de la extensión → el agente atiende → Anura disca al número.
Por eso el softphone o teléfono del agente tiene que estar conectado.

**Errores (HTTP 40X, JSON):**

| Respuesta de Anura | Significa | Mensaje que muestra el CRM |
|---|---|---|
| 400 `No Originate terminal for extension` | La extensión no tiene terminal principal | "Tu extensión no tiene una terminal principal en Anura…" |
| 400 `Originate Terminal is not registered` | El softphone/teléfono no está conectado | "Tu teléfono de Anura no está conectado…" |
| 401 `SecretId [n] not found` | Token inválido | "Anura rechazó el token de Click2Dial…" |

**Truco para validar el token sin llamar a nadie:** hacer el POST con una extensión inexistente. Con token válido
responde 400 ("No Originate terminal"); con token inválido, 401. Es lo que hace el botón *Verificar token*.

**Unificar la llamada con sus eventos:** el CRM crea su registro de llamada **antes** de llamar y manda su id en
`customs` (`crm-<id>`). Cuando llega el evento, `{{ custom1 }}` trae ese id y la llamada se asocia sin ambigüedad.
(Soporte de Anura cree que el id que devuelve Click2Dial es el mismo `cdrid` del evento, pero lo está confirmando con
desarrollo; con `custom1` no hace falta depender de eso.)

---

## 4. Recibir las llamadas: WebHooks (Eventos)

Documentación: <https://kb.anura.com.ar/es/articles/2579270-webhooks> ·
variables: <https://kb.anura.com.ar/es/articles/2579414-variables-eventos-templetizados>

### Dónde y cómo se configuran

> **Ojo:** la plantilla y los triggers tienen un tilde **Activo**. En la primera prueba real no llegaba nada porque
> estaban desactivados: Click2Dial respondía 200 pero no había ningún POST en el log del CRM.

Son **dos pasos** (requiere rol de configuración):

**1. Plantilla (una sola):** *Configuración → Eventos → Agregar*. Ahí va la **petición** y el **cuerpo**:

| Campo | Valor |
|---|---|
| Protocolo / Puerto | HTTPS / 443 |
| Host | dominio del CRM (ej. `crm.cliente.com.ar`) |
| Ruta | `api/integrations/anura/webhook` (sin barra inicial) |
| Método | POST |
| Content-Type | JSON |
| Autorización | Bearer `<token del CRM>` (se ve y copia en *Telefonía Anura*) |
| Cuerpo | la plantilla de abajo |

Si no se quiere usar el header, sirve la ruta con el token: `api/integrations/anura/webhook?token=<token del CRM>`.

**2. Triggers, en cada cuenta:** *Configuración → Cuenta* → elegir la cuenta (interno del agente, o la que recibe las
entrantes / cola) → **Modificar** → pestaña **Eventos** → **Agregar**, tres veces, todas con la misma plantilla:

| Nombre | Evento | Dirección | Plantilla | Para qué |
|---|---|---|---|---|
| CRM inicio | `START` | `BOTH` | la del paso 1 | La llamada empieza a sonar (aviso en pantalla si ya se sabe el agente) |
| CRM atendida | `TALK` | `BOTH` | la del paso 1 | Alguien atendió: define quién es el dueño de la llamada |
| CRM fin | `END` | `BOTH` | la del paso 1 | Duración, resultado, grabación; dispara tareas y avisos |

Etiquetas y Filtros vacíos, Activo tildado. Una cuenta sin estos triggers no le avisa nada al CRM (sirve para limitar
qué llamadas llegan, por ejemplo en una cuenta compartida con otras áreas).

Fuente: <https://kb.anura.com.ar/es/articles/2579270-webhooks>

### Cuerpo de la plantilla

```json
{
  "callId": "{{ cdrid }}",
  "event": "{{ hooktrigger }}",
  "direction": "{{ direction }}",
  "status": "{{ status }}",
  "calling": "{{ calling }}",
  "callingName": "{{ callingname }}",
  "called": "{{ called }}",
  "dialTime": "{{ dialtime }}",
  "billSeconds": "{{ billseconds }}",
  "wasRecorded": "{{ wasrecorded }}",
  "answerExtension": "{{ answerextension }}",
  "answerTerminal": "{{ answerterminal }}",
  "queueAgentExtension": "{{ queueagentextension }}",
  "accountExtension": "{{ accountextension }}",
  "accountName": "{{ accountname }}",
  "queueId": "{{ queueid }}",
  "queueName": "{{ queuename }}",
  "custom1": "{{ custom1 }}",
  "recordingUrl": "{{ audio_file_mp3 }}"
}
```

La plantilla vive en el código (`PLANTILLA_WEBHOOK` en `app/apps/telefonia/services.py`) y la pantalla la muestra lista
para copiar. Si se cambia, cambiarla en un solo lugar.

### Variables más útiles de Anura

| Variable | Qué trae |
|---|---|
| `{{ cdrid }}` | ID de la llamada (clave de idempotencia) |
| `{{ hooktrigger }}` | START / TALK / END |
| `{{ direction }}` | IN / OUT |
| `{{ calling }}` / `{{ called }}` | Origen / destino |
| `{{ status }}` | Estado de la llamada |
| `{{ billseconds }}` / `{{ duration }}` | Segundos facturables / duración total |
| `{{ answerextension }}` / `{{ answerterminal }}` | Quién atendió (disponibles con la llamada atendida o al final) |
| `{{ queueagentextension }}` / `{{ queueagentname }}` | Agente de la cola que atendió |
| `{{ accountextension }}` / `{{ accountname }}` | Cuenta del evento (en salientes, la del agente que llama) |
| `{{ queueid }}` / `{{ queuename }}` | Cola |
| `{{ custom1 }}` … | Valores enviados en Click2Dial |
| `{{ audio_file_mp3 }}` | Link de descarga de la grabación |

Lista completa: ver el artículo de variables.

---

## 5. Cómo interpreta el CRM los eventos

Todo pasa por una sola función, `procesar_evento_llamada` (`app/apps/telefonia/services.py`), que usan el webhook, el
modo demo, el discador y el polling opcional.

1. **Idempotencia:** por `cdrid`. Si Anura reenvía un evento, no se duplica nada.
2. **Llamada del CRM:** si `custom1` = `crm-<id>`, se asocia a ese registro.
3. **Número externo:** en IN es `calling`; en OUT es `called`. Se normaliza igual que WhatsApp
   (`011 15…`, `+54 9…`, `11…` → `+549…`) para encontrar al contacto.
4. **Agente:** se busca por *interno o alias*, en este orden: quién atendió (`answerExtension`, `answerTerminal`,
   `queueAgentExtension`) → cuenta del evento (`accountExtension`, `accountName`). Quien atendió siempre prevalece.
5. **Estados:** `START` → sonando · `TALK` → en curso (atendida) · `END` → final (atendida si hubo segundos
   facturables o `status` = ANSWER; si no, no atendida / ocupado según `status`).
6. **CRM:**
   - Número conocido → la llamada se suma a su ficha / oportunidad.
   - Número nuevo → contacto + oportunidad en el embudo del DID/cola (o el embudo por defecto). En entrantes la
     tarjeta **nace sin dueño** y es **de quien atiende**; si nadie atiende, se asigna por la regla del embudo.
   - Saliente desde el softphone a un número nuevo → también crea la tarjeta (configurable).
   - No atendida → notificación "Llamada perdida" + tarea "Devolver llamada" (prioridad alta).
   - Saliente → cuenta como intento de contacto; la primera gestión mueve la tarjeta a la segunda etapa.
7. **Grabación:** con el evento END se encola la descarga del link `recordingUrl` y se adjunta a la ficha.

---

## 6. Configuración en el CRM (pantalla *Telefonía Anura*)

| Campo | Valor |
|---|---|
| Integración activa | Sí |
| Modo demostración | **No** (en sí simula las llamadas, útil para capacitar) |
| Dirección del Anura web | La que usan los agentes → muestra el botón "Ir a Anura" |
| Token de Click2Dial | El que generó el administrador |
| URL de Click2Dial | `https://api.anura.com.ar/adapter/default/click2call` (Perú: `api.anura.pe`) |
| Embudo para números desconocidos | El embudo por defecto de las entrantes |
| Crear tarjeta en salientes a números nuevos / Tarea en llamada perdida / Descargar grabaciones | Sí |
| API de tenant / Polling de CDRs | Dejar vacío / apagado salvo que Anura habilite esa API |

**Por usuario** (*Usuarios → usuario* o *Telefonía Anura → Internos*): **Interno** = extensión de Anura;
**Alias** = otros identificadores con los que Anura lo nombra en los eventos (usuario, nombre de cuenta, terminal),
separados por coma. Un interno o alias no puede repetirse entre usuarios.

**Rutas** (opcional): DID o cola → embudo (y opcionalmente un agente fijo). Solo decide **dónde se crea la tarjeta**;
a qué teléfono suena la llamada lo decide la central de Anura.

---

## 7. Experiencia del agente

- Tiene que tener su teléfono de Anura conectado (softphone web o de escritorio, app o teléfono IP).
- Toca **Llamar** en la ficha / tablero / lista / inbox / Mi día → suena su teléfono → atiende → se disca al cliente.
  **Recomendado:** en Anura, *Configuración → Cuentas → (cuenta del agente) → Avanzado*, activar **Auto answer** y
  **Activar Click 2 Dial**: la terminal atiende sola las llamadas que origina Click2Dial, así el agente toca Llamar y
  ya queda sonando el cliente en su auricular (clave para el discador). No afecta a las entrantes.
- Un **aviso flotante** muestra con quién habla y el tiempo, aunque cambie de pantalla, con el botón **Ir a Anura**.
- **Corta desde su teléfono** (o corta el cliente); el aviso se cierra solo al llegar el evento END.
- En entrantes por cola, el aviso con la ficha aparece **al atender** (mientras suena, Anura no sabe quién atenderá).

---

## 8. Prueba de puesta en marcha (checklist)

1. Cargar token y verificar (*Verificar token* → "Token válido").
2. Cargar internos (y alias) de 1–2 agentes; que tengan el softphone abierto.
3. Crear la plantilla en Anura y cargar los tres triggers en esas cuentas y en la cola/DID de prueba.
4. **Saliente desde el CRM** a un celular propio: suena el softphone, se disca, se habla, se corta → en la ficha:
   llamada atendida con duración, intento registrado, grabación.
5. **Saliente desde el softphone** a un número que no está en el CRM → se crea la tarjeta asignada a ese agente.
6. **Entrante atendida** desde un celular nuevo → se crea la tarjeta, asignada a quien atendió.
7. **Entrante no atendida** → tarjeta asignada por la regla del embudo, aviso de llamada perdida y tarea.
8. En cada caso revisar el **payload** guardado (Django admin → *Llamadas* → campo `payload`, tiene cada evento por
   trigger) y confirmar los valores reales de `status` y que el identificador del agente coincida con interno/alias.

---

## 9. Pendiente de confirmar con Anura

- Valores exactos de `{{ status }}` (el CRM acepta ANSWER/ANSWERED, NOANSWER/NO_ANSWER, BUSY, FAILED, CANCEL…, y si
  no reconoce el valor decide por los segundos facturables).
- Si el link de `{{ audio_file_mp3 }}` se descarga sin credenciales (el CRM lo intenta sin y, si es rechazado, con las
  de la API de tenant si están cargadas).
- ~~Si la respuesta de Click2Dial trae algún id~~: responde 200 con cuerpo vacío (no hace falta: se usa `custom1`).
- Si el WebPhone de Anura puede integrarse en una web propia (permitiría atender y cortar desde el CRM).

---

## 10. Dónde está el código (para reutilizar)

| Archivo | Qué hace |
|---|---|
| `app/apps/telefonia/client.py` | Cliente de Anura: `dial` (Click2Dial), `verificar_token`, traducción de errores, descarga de grabaciones, cliente demo |
| `app/apps/telefonia/services.py` | `normalizar_evento` (variables de Anura → modelo interno), `procesar_evento_llamada` (idempotencia, CRM, dueño), `discar`, discador progresivo, `PLANTILLA_WEBHOOK` |
| `app/apps/telefonia/views.py` | Webhook (`/api/integrations/anura/webhook`, token por URL, header o Bearer), `/api/telephony/dial`, estado para el aviso flotante, configuración |
| `app/apps/telefonia/tasks.py` | Descarga de grabaciones con reintentos, discador (cada 10 s), cierre de llamadas colgadas, simulador demo |
| `app/apps/telefonia/models.py` | `ConfigAnura`, `InternoAnura` (interno + alias), `RutaAnura`, `Llamada`, campañas de discado |
| `app/apps/telefonia/tests.py` | Tests con la plantilla real renderizada (START → TALK → END, colas, errores, Bearer) |
| `app/core/phone.py` | Normalización de teléfonos argentinos (misma clave para WhatsApp y Anura) |

Para otro proyecto: copiar `client.py` + `normalizar_evento`/`procesar_evento_llamada` y adaptar la parte "CRM" de
`_vincular_crm` / `_al_finalizar` a los modelos de ese proyecto. La plantilla de eventos y los pasos del panel son los mismos.
