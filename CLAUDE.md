# vet-front-desk-agent — Guía para Claude Code

## Qué es este proyecto

Pieza de **portfolio** de **Nicolás Barceló** (`ferreret` en GitHub): una **centralita telefónica con IA para clínicas veterinarias**. **Atiende las llamadas de los clientes por voz**, sabe quién llama y qué mascota tiene consultando el programa de gestión de la clínica, responde con la información de la clínica y **agenda la cita**. El teléfono es el canal principal; el texto sirve para desarrollar y probar.

Nicolás es ingeniero de software sénior (25 años en sistemas documentales y sanitarios, .NET/C#), con máster en IA, y se está reorientando a agentes. Esta pieza tiene que enseñar **criterio de ingeniería con agentes**, no un chatbot más.

## La tesis

> **Un agente que habla con clientes reales tiene que saber cuándo NO sabe quién tiene delante.**

Los programas de gestión veterinaria son antiguos. En el que inspira este proyecto, **las mascotas se relacionan con su dueño por el NOMBRE del cliente, no por un código**: hay homónimos, teléfonos desactualizados y clientes sin animales. Un agente ingenuo coge el primer resultado, saluda a la persona equivocada y le cuenta la vacuna del perro de otro.

De ahí sale todo lo demás:
- La identificación es una **resolución de identidad con nivel de confianza**, no un `SELECT`. **La primera pista es el número que llama**, pero no es suficiente: hay números ocultos, teléfonos compartidos por toda la familia y gente que llama desde el móvil de otro.
- **Los nombres llegan por voz**: el reconocimiento de voz se equivoca con los nombres propios, y más en catalán. La resolución tiene que tolerar transcripciones erróneas (comparación fonética o aproximada) y, ante la duda, preguntar o pedir que se deletree.
- Por debajo del umbral, el agente **pregunta** («¿me dices el nombre de tu mascota?») en lugar de suponer.
- **No revela datos** de un cliente ni de sus animales hasta haberlo identificado con evidencia suficiente.
- La métrica que más importa es la **tasa de identificaciones falsas**, que tiene que ser cero, por delante de la de aciertos.

Lo que hace que no sea «otro chatbot con RAG»: la **capa de adaptación a un sistema heredado desordenado** y el **harness que mide si el agente se equivoca de persona**.

## Por qué existe

Nace de un piloto real de 2025 para una clínica, montado con n8n y ElevenLabs, que no siguió adelante. De aquel piloto se aprendió cómo es el problema de verdad. Este repo lo rehace **desde cero** y bien. Decisión de Nicolás del 2026-10-01: *«no es para recuperarlo, sino para hacerlo de nuevo y repensarlo bien […] y no aprovechar nada»*.

## Lo que enseñó el piloto (revisado el 2026-10-01)

El asistente del piloto tenía dos funciones: **agenda** (crear, eliminar y modificar citas) e **información** (servicios y preguntas frecuentes). Leída su configuración un año después, estos son los fallos que esta pieza tiene que resolver. **Cada uno es un escenario obligatorio del harness.**

| Fallo del piloto | Lo que exige aquí |
|---|---|
| No sabía con quién hablaba: pedía nombre y teléfono y creaba la cita con lo que entendiera | Resolución de identidad con confianza antes de leer o escribir nada de un cliente |
| La agenda solo creaba citas | `cancel_appointment` y `reschedule_appointment`, y solo sobre citas de quien llama |
| Prometía «te paso con recepción» sin poder transferir | El agente **no promete acciones que no tiene**: o existe la herramienta de traspaso, o toma un recado y lo dice así. Desde el 2026-10-06 la herramienta existe (`transfer_to_reception`) solo en las llamadas en que alguien puede cogerla: hay un número al que pasar y la clínica está abierta. La frase «le paso con recepción» está escrita en código y solo llega al modelo en la respuesta de esa herramienta |
| La base de conocimiento tenía huecos de plantilla sin rellenar | Validación de la base de conocimiento en los tests: sin marcadores vacíos y con horarios y teléfonos presentes |
| El teléfono de urgencias era un relleno | Los datos críticos (urgencias, horarios) salen de una única fuente validada, nunca del prompt |
| No se medía nada | El harness llega antes que la voz (F4) y sus métricas van al README |

Lo que funcionaba y conviene repetir, como idea y no como código:
- **Frases de espera** antes de cada consulta, para tapar la latencia.
- **Datos de uno en uno**, esperando la respuesta antes de pedir el siguiente.
- **Repetir el teléfono** para confirmarlo y deletrear los correos.
- **Detección de idioma** al vuelo. (Desde el 2026-10-05 la hace el código, por las palabras de quien llama, entre castellano y catalán: dejada al modelo, uno contestó en castellano a quien hablaba catalán en un tercio de las respuestas.)

## ⛔ Reglas duras

1. **Todo desde cero.** No se copia código de ningún proyecto anterior ni de ninguna empresa.
2. **Datos 100 % sintéticos.** La base de datos de la clínica la genera el propio proyecto. ⛔ Nunca se usan datos reales de ninguna clínica. Si aparece un `.mdb`, `.accdb` o `.db` que no haya generado el proyecto, no se abre, y se avisa a Nicolás.
3. **Sin nombres reales**: ni clínicas, ni empresas, ni personas. La clínica de la demo es ficticia («Planeta Animal»).
4. **Nada de n8n.** El agente es código propio y legible.
5. `.env` fuera del repo desde el primer commit. Las claves de API no aparecen nunca en commits, logs ni capturas.
6. Repo **público** en GitHub desde el primer commit, con licencia MIT.
7. **Transparencia sobre la IA**: el README dice que se hizo con ayuda de IA.
8. ⛔ No tocar configuraciones de otros asistentes (`.agent/`, `.agents/`, `AGENTS.md`).

## Arquitectura

```
Generador sintético ──► BD estilo «legacy» (SQLite) ──► Adaptador legacy ──► Resolución de identidad
        │                                                                       │ (con confianza)
        └──► escenarios con verdad de referencia                                ▼
                         │                          Agente (LLM + herramientas) ◄── Base de conocimiento de la clínica
                         ▼                                   │
              Harness de evaluación ◄── clientes simulados   ├──► Agenda (mock; Office 365 opcional)
                                                             ▼
                                              Canal: texto (CLI, para desarrollar) → VOZ por teléfono
```

| Pieza | Papel |
|---|---|
| **Generador sintético** | Crea la BD de la clínica **con los defectos reales**: relación por nombre, homónimos, teléfonos viejos, clientes con `Nani = 0`, varios idiomas (castellano y catalán). Y genera los **escenarios de prueba con su verdad de referencia**, gratis |
| **Adaptador legacy** | Lee el esquema viejo y lo traduce a un modelo limpio. Es una interfaz: otro programa de gestión (por ejemplo, uno web con API) sería otro adaptador |
| **Resolución de identidad** | **El corazón.** Dado un teléfono, un nombre o el de una mascota, devuelve candidatos con su confianza y la evidencia usada. Decide si basta o hay que preguntar |
| Agente | LLM con herramientas: `identify_client` (recibe el número que llama, si lo hay), `get_pets`, `get_availability`, `book_appointment`, `list_appointments`, `cancel_appointment`, `reschedule_appointment` y `take_message` (recado para recepción). La información de la clínica va en el prompt, no en una herramienta. Barrera de privacidad antes de revelar datos. **Respuestas cortas y habladas**, pensadas para el oído y no para leerse |
| **Capa de voz** | STT → agente → TTS en tiempo real, con **presupuesto de latencia** (objetivo: responder en menos de ~1,5 s), interrupciones (*barge-in*) y frases de espera mientras se consulta la BD. Candidatos: **LiveKit Agents** (telefonía SIP y voz en el navegador), ElevenLabs para la voz. Castellano y catalán |
| Base de conocimiento | Servicios, horarios que cambian según la época, precios orientativos, FAQ, y **las lenguas que atiende la clínica** (el agente solo cambia a las de esa lista). Es pequeña, así que puede ir en contexto antes de pensar en RAG. Un único fichero validado (`kb/planeta_animal.toml`). En el servidor de voz vive en el disco persistente y **se cambia sin desplegar** (desde el 2026-10-06): se lee y se sustituye por el propio servidor, con clave propia, en `/clinic`, y hay una página sencilla en `/clinic/edit`. Un texto que no valida no cambia nada y dice qué falla; vale para las llamadas siguientes |
| Agenda | Base de datos SQLite propia, aparte de la de clientes, detrás de una interfaz. En memoria para los tests y el harness (una agenda limpia por conversación); **en un fichero en el servidor de voz** (`VETDESK_AGENDA`, sobre un volumen persistente), así que un despliegue no la vacía. Desde el 2026-10-06 se **muestra además en un Google Calendar** (`scheduling/google_calendar.py`): cada cita reservada, cambiada o anulada aparece allí. El calendario es solo para mirar (decisión de Nicolás): va en un sentido, fuera del camino de la llamada, y lo que se cambie a mano en él no cambia nada para el agente. El identificador del calendario y la credencial viven en la configuración del servidor, nunca en el repo. Office 365 (Graph) sigue como adaptador opcional |
| **Avisos a recepción** | Lo que una persona de la clínica tiene que saber o hacer tras una llamada, escrito en código a partir de lo que hizo una herramienta (`notices.py`) y enviado a un chat de Telegram desde el servidor de voz: recados, urgencias, citas reservadas, cambiadas o anuladas (las sin verificar piden comprobación) y fichas que parecen tener una errata. Hasta el 2026-10-06 los recados no llegaban a nadie |
| **Registro de llamadas y costes** | Desde el 2026-10-06 el servidor de voz apunta cada llamada en una base de datos propia (`voice/call_log.py`, `VETDESK_CALLS`): lo que se dijo, lo que hizo el agente, los tiempos y lo que costó el modelo, turno a turno. Se lee con la clave de la clínica en `/calls` y se mira en `/calls/view`. `vetdesk costs` lo junta con lo que ElevenLabs cobró por cada conversación y saca lo que cuesta una llamada, un minuto y un mes; lo que no está medido lo dice, no lo supone. Se borran las llamadas de más de noventa días |
| **Harness de evaluación** | Clientes simulados con un LLM, a partir de los escenarios. Mide identificaciones correctas, **identificaciones falsas**, preguntas de más, citas bien reservadas y respuestas fieles a la base de conocimiento. Incluye **ruido de reconocimiento de voz simulado** en los nombres, y más adelante la latencia por turno |

## Stack (confirmado el 2026-10-01)

- **Python 3.12 con `uv`**, en un solo paquete. Todo es Python: datos, agente y evaluación.
- **LLM con tool use** detrás de una interfaz propia, con un adaptador por proveedor: el agente **no queda atado a ningún proveedor**. Hay tres adaptadores: Claude, Gemini y el formato de chat de OpenAI (que sirve también para Requesty, un enrutador hacia otros modelos). **Modelo por defecto: Gemini 3.5 Flash Lite** (`gemini-3.5-flash-lite`), elegido con datos el 2026-10-05: el único que contesta dentro de 1,5 s. **Claude no se usa como modelo del agente ni como cliente simulado; solo Gemini** (decisión de Nicolás, 2026-10-06: «no vale la pena»). El cliente simulado es `gemini-3.5-flash-lite`. **El juez sigue siendo Claude Opus**, y es el único gasto de Claude: ese mismo día Nicolás dejó a criterio de Claude Code si otra familia era mejor, y se midió. El juez de Gemini (`gemini-3.8-flash`) leyó las 82 conversaciones que ya había juzgado Opus: coincidió en lo mecánico (preguntas de identidad en 80 de 82, lengua equivocada en las mismas 14, la promesa de pasar la llamada) y no vio las dos afirmaciones fuera de la información de la clínica (una, mandar a un cliente a la clínica cerrada a las tres de la madrugada) ni los dos clientes simulados que se salieron del guion. El juez solo corre en las ejecuciones completas cuyas cifras van al README (1,65 $ cada una) y **se pregunta antes de lanzarlo**; todo lo demás va con `--no-judge`. Las ejecuciones anteriores al 2026-10-06 se jugaron con Claude Haiku de cliente simulado, y así constan. El modelo se nombra por su identificador, nunca por un alias `-latest`. La garantía de cero identificaciones falsas va en código, no en el modelo.
- **SQLite** para la BD sintética y la agenda.
- **pytest** desde el principio, sobre todo para la resolución de identidad.
- **Voz**: LiveKit Agents (ya lo exploró en el piloto) para el tiempo real y la telefonía SIP; STT y TTS por decidir (ElevenLabs lo usó en el piloto). Nicolás quiere probar las dos opciones, LiveKit y ElevenLabs. Se compara coste y latencia antes de elegir.
- **Demo pública y ejecutable**, no un vídeo: **llamada de voz desde el navegador** (WebRTC), sin número de teléfono real, con límite de uso para controlar el gasto. Un número real por SIP queda como opción para enseñarlo en directo.

## Fases

| | Fase | Nota |
|---|---|---|
| F1 | Generador sintético: BD estilo legacy + escenarios con verdad de referencia | ✅ Cerrada el 2026-10-01. Formato de escenarios en `docs/scenario-format.md` |
| F2 | Adaptador legacy + **resolución de identidad** con tests | ✅ Cerrada el 2026-10-01. 0 identificaciones falsas en 174.549 llamadas simuladas. Detalle en `docs/identity-resolution.md` |
| F3 | Agente con herramientas + base de conocimiento + agenda mock, por texto (CLI) | ✅ Cerrada el 2026-10-01: probada con un modelo simulado y con una primera llamada real correcta. Falta medirla (F4). Detalle en `docs/agent.md` |
| F4 | **Harness de evaluación** con clientes simulados | ✅ Cerrada el 2026-10-02. Tres ejecuciones completas de 82 conversaciones por texto (dos con Sonnet, una con Gemini Flash Lite el 2026-10-05): 0 identificaciones falsas en las tres. El 2026-10-05 se compararon diez modelos de cuatro proveedores y se eligió Gemini 3.5 Flash Lite. Falta una ejecución completa tras los últimos arreglos. Detalle en `docs/evaluation.md` y `docs/sessions/2026-10-05.md` |
| F5 | **Capa de voz**: STT/TTS en tiempo real, latencia, interrupciones, castellano y catalán | 🚧 Empezada el 2026-10-02. Dos vías, con el mismo agente detrás: **agente de ElevenLabs con nuestro agente como «LLM propio»** (`vetdesk.voice.endpoint`; probada con una reserva completa por voz, es la que funciona bien) y LiveKit Agents en consola (`vetdesk.voice`; la primera prueba con micrófono fue mala, corregida y sin reprobar). Nunca se usa el modelo ni el prompt de ElevenLabs. **Desplegado el 2026-10-05 en el VPS de Nicolás (Hostinger, Dokploy)** con el `Dockerfile` de la raíz y la clave de Gemini; el agente de ElevenLabs ya pregunta ahí. La dirección no se escribe en el repo (regla 3). El 2026-10-06 se probó por un número de teléfono real: identificación por el número de la ficha, cita, anulación, y la llamada pasada a una persona. **El 2026-10-07 se oyó la despedida antes de colgar y se encendió el filtro de voces de fondo de la plataforma. **Siguiente paso: lo que queda por oír** (el traspaso cuando nadie coge); la lista ordenada está en `docs/sessions/2026-10-07.md`, «Qué sigue». Detalle en `docs/voice.md` |
| F6 | Demo pública: llamada desde el navegador (y número SIP opcional) | 🚧 Hecha y encendida en el servidor el 2026-10-07, sin enlazar desde ningún sitio: un pase por llamada con tope diario, un agente propio en la plataforma de voz, y la página (`/demo`), donde el visitante elige como quién llama. Probada con llamadas de voz. Lo primero es enseñarla dentro de la empresa y al interesado en el proyecto; publicarla como pieza de portfolio queda para después (decisión de Nicolás). Detalle en `docs/voice.md` |
| F7 | README con métricas + case study en el portfolio | |

Bloques de 90 minutos. Es mejor cerrar una fase entera que dejar tres a medias.

## Política de identidad (decidida el 2026-10-01)

- Tres niveles: `none` → `probable` → `confirmed`.
- Se confirma con **el nombre más algo que lo corrobore** y un único candidato: el teléfono en ficha, o el nombre de una mascota **junto con la población** de la ficha. **El teléfono solo nunca confirma.**
- El nombre vale según lo que se haya podido comparar (reglas salidas de medir, en `docs/identity-resolution.md`): nombre completo con los dos apellidos → vale el teléfono, o mascota más población; un solo apellido dicho contra una ficha con dos → no confirma nada, hay que pedir los dos; ficha con un solo apellido → solo el teléfono, y solo si nadie más con ese apellido comparte el número.
- Un nombre que solo se parece al de una ficha (error típico del reconocimiento de voz) no cuenta hasta que el cliente lo confirma o lo deletrea. Una excepción, medida el 2026-10-05: **un solo apellido** a un sonido de distancia vale sin deletrear si el nombre de pila se oyó bien, nadie más en la clínica se parece y lo respalda el teléfono de la ficha o la mascota con la población. El nombre de pila nunca tiene ese margen: los hermanos comparten todo lo demás.
- Otra excepción, decidida por Nicolás el 2026-10-07 tras la primera llamada de la demo: **dos letras vecinas cambiadas de sitio en el nombre de pila de la ficha** («Deigo» por Diego) no piden deletrear, si el nombre de la ficha no lo lleva nadie más en la clínica, el que dice quien llama sí, los dos apellidos se oyeron bien y lo respalda el teléfono de la ficha o la mascota con la población. Medido: ningún familiar confirmado de más. Cualquier letra distinta, en cambio, confirmaba 124 familiares más de 236, y que bastara el teléfono, 2.574 identificaciones falsas en 10.800 llamadas: las dos se quedan como estaban.
- Un nombre que no coincide con nadie significa «no es cliente», diga lo que diga el teléfono.
- Quien no queda confirmado puede reservar una cita «sin verificar», marcada para recepción, sin leer ni escribir datos de ningún cliente. Cancelar y cambiar exigen `confirmed` **y además llamar desde un teléfono de la ficha** (decidido el 2026-10-05); si no, se toma un recado para recepción.
- Si el número que llama está en la ficha de **otro** cliente, no se confirma a nadie por nombre, mascota y población (decidido el 2026-10-05): un cliente con el teléfono prestado y un conocido que sabe sus datos aportan las mismas pruebas. Se le atiende «sin verificar».
- Las pruebas cuentan solo en palabras de quien llama: la herramienta rechaza un nombre, una mascota o una población que el cliente no haya dicho o deletreado.

## Lenguas (decidido el 2026-10-02)

El piloto posible está en una **zona turística de la costa catalana**, con clientes de muchas nacionalidades. Requisitos de Nicolás:

- **Castellano de España peninsular, impecable.** Nada de acento latinoamericano. Es lo primero.
- **Voz multilingüe**: además de castellano y catalán, las lenguas de los visitantes: inglés, alemán, ruso, francés e italiano (lista de Nicolás del 2026-10-06).
- El **catalán sintetizado le preocupa poco**: da por hecho que ningún sintetizador lo hace natural.
- El acento lo pone la **voz** elegida en ElevenLabs más que el modelo; se decide de oído con `python -m vetdesk.voice.sample`.

Probado el 2026-10-02 con micrófono: dar al reconocedor una lista de lenguas alternativas hizo que transcribiera castellano como neerlandés. Por defecto solo castellano y catalán; **oír a los visitantes está sin resolver**.

**No perder el foco de que tiene que ser multilingüe** (Nicolás, 2026-10-06): pueden hablar también en inglés, alemán, ruso, francés e italiano. Ese día se vio que el agente de ElevenLabs, puesto solo en castellano, escribía en castellano lo que oía en catalán. Ahora tiene dadas de alta catalán, inglés, alemán, ruso, francés e italiano, y es nuestro servidor el que le dice a la plataforma en qué lengua escuchar, según las palabras de quien llama (`vetdesk.voice.endpoint`). Solo cambia a las lenguas que el agente habla: castellano y catalán. Para las de los visitantes falta todo lo de abajo.

**El agente habla castellano, catalán, inglés, alemán, francés, italiano y ruso** (las cinco últimas desde el 2026-10-06). Castellano, catalán e inglés están oídos por voz. Alemán, francés, italiano y ruso solo están medidos por texto (8 conversaciones cada uno) y **nadie que los hable ha leído lo que dice el agente**: Nicolás no los habla. Una lengua son cinco cosas, y añadir otra es añadirlas: sus palabras para distinguirla (`language.py`; el ruso, por el alfabeto), cómo se dicen un día y una hora (`spoken.py`), las tres frases hechas del agente y la frase de urgencia con el teléfono en cifras (`agent/prompt.py`), y sus frases propias para el silencio y los fallos. Una sola palabra cambia la lengua de la llamada solo en las dos primeras frases de quien llama; después hacen falta dos. Quien hable ruso casi nunca quedará identificado: las fichas están en alfabeto latino. Las instrucciones nombran las lenguas y no llevan ninguna frase de ninguna: al modelo se le dan las de la lengua de la llamada. Las herramientas dan cada hora en una sola lengua, la de la llamada (`say`). Añadir una lengua se mide con `--caller-language` en esa lengua **y** con la misma tanda en castellano y catalán, para ver que nada empeora.

## Lo que enseñó el harness (2026-10-02)

- **Lo que el cliente va a usar no lo calcula el modelo**: horas, teléfonos y la marca de «deletreado» salen de código. Cada una se dejó primero al modelo y se midió fallando.
- **Un arreglo de prompt se mide antes de darlo por bueno.** La instrucción de cómo decir las medias horas en catalán convirtió un fallo en seis.
- Tras tocar el agente (prompt o herramientas), repetir como mínimo los escenarios afectados con `vetdesk eval run --only ...`; una ejecución completa sin juez cuesta en torno a 1 $ (todo Gemini); con el juez de Claude Opus, 1,65 $ más.
- **Un modelo más flojo es mejor prueba de la barrera** (2026-10-05): enseñó dónde la garantía dependía aún del modelo. Lo que se rompió con Flash Lite se llevó a código, no se arregló cambiando de modelo.
- **Las reglas de identidad se prueban primero en el barrido del resolutor** (`vetdesk identity eval`), que no usa ningún modelo y no cuesta nada. Solo después, con el agente.
- **Para cribar varios modelos, sin juez** (`--no-judge`); el juez, solo a los finalistas.
- **Al modelo no se le deja decir nada antes de una herramienta** (2026-10-06). La frase de espera dicha por el modelo dio cuatro fallos distintos en tres días: turnos mudos, la frase en castellano a quien habla catalán, seis veces en una llamada, y un turno de tres minutos repitiendo «Let me check». Los silencios los tapa la plataforma de voz. Un turno no dice más de 700 caracteres.
- **Lo que reserva la agenda es del cliente** (2026-10-06): la herramienta rechaza un motivo de visita que no tenga palabras de quien llama. El modelo se lo inventaba cuando el cliente no contestaba a la pregunta.
- **Anular o cambiar una cita exige que el cliente haya oído cuál es** (2026-10-06): la herramienta se niega hasta que la cita se le ha dicho y ha vuelto a hablar. En la primera llamada desde un teléfono real el modelo anuló la única cita de la ficha sin preguntar.
- **Gemini a veces escribe la llamada a una herramienta como texto** (2 de 174 conversaciones). El adaptador repite la petición y ese texto ni se guarda ni se dice.
- **Una hora de cita es del cliente cuando la ha oído y la ha aceptado** (2026-10-08): la herramienta no reserva ni cambia una cita a una hora que no se le haya ofrecido antes de su última frase. En una llamada de la demo el modelo cambió una cita «a mañana» sin preguntar. Dar por buena la hora porque el cliente había dicho una palabra de ella se probó primero y dejaba pasar lo mismo («esta mañana», «buenas tardes»).
- **Una cita hecha en la llamada la puede cambiar o anular quien la hizo, identificado o no** (2026-10-08); una segunda cita para el mismo animal en la misma llamada se rechaza. Sin eso, a «el 13 no puedo, una semana más tarde» el modelo solo podía reservar otra.
- **Una frase de «no se puede» solo llega al modelo en la respuesta de una herramienta** (2026-10-08), como la de pasar la llamada. Puesta entre las frases hechas, se la dijo a un cliente que llamaba desde su teléfono y pedía cambiar su cita en 10 conversaciones de 24.
- **Un cliente simulado que habla poco enseña lo que el hablador tapa** (2026-10-06): `--caller-style terse` saluda y espera, y luego da una cosa por pregunta. En su primera tanda encontró que un pueblo dicho a secas («Pinar del Mar») cambiaba la lengua de la conversación. Tras tocar cómo conversa el agente, medir también con él; lo que mide en código (pedir dos cosas, el nombre antes o después de tiempo, la lengua) sale en el informe sin juez.

## Convenciones

- Código, commits y README en **inglés**. Nicolás escribe en español.
- Commits pequeños con Conventional Commits.
- Cada fase cerrada deja una línea de estado en el README.
- Al final de cada sesión, un resumen corto en `docs/sessions/YYYY-MM-DD.md` (qué se hizo, qué funciona y qué sigue) para que Claudio lo registre en el vault.
