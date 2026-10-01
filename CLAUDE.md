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
| Prometía «te paso con recepción» sin poder transferir | El agente **no promete acciones que no tiene**: o existe la herramienta de traspaso, o toma un recado y lo dice así |
| La base de conocimiento tenía huecos de plantilla sin rellenar | Validación de la base de conocimiento en los tests: sin marcadores vacíos y con horarios y teléfonos presentes |
| El teléfono de urgencias era un relleno | Los datos críticos (urgencias, horarios) salen de una única fuente validada, nunca del prompt |
| No se medía nada | El harness llega antes que la voz (F4) y sus métricas van al README |

Lo que funcionaba y conviene repetir, como idea y no como código:
- **Frases de espera** antes de cada consulta, para tapar la latencia.
- **Datos de uno en uno**, esperando la respuesta antes de pedir el siguiente.
- **Repetir el teléfono** para confirmarlo y deletrear los correos.
- **Detección de idioma** al vuelo.

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
| Agente | LLM con herramientas: `identify_client` (recibe el número que llama, si lo hay), `get_pets`, `search_clinic_info`, `get_availability`, `book_appointment`, `cancel_appointment`, `reschedule_appointment` y `take_message` (recado para recepción). Barrera de privacidad antes de revelar datos. **Respuestas cortas y habladas**, pensadas para el oído y no para leerse |
| **Capa de voz** | STT → agente → TTS en tiempo real, con **presupuesto de latencia** (objetivo: responder en menos de ~1,5 s), interrupciones (*barge-in*) y frases de espera mientras se consulta la BD. Candidatos: **LiveKit Agents** (telefonía SIP y voz en el navegador), ElevenLabs para la voz. Castellano y catalán |
| Base de conocimiento | Servicios, horarios que cambian según la época, precios orientativos, FAQ. Es pequeña, así que puede ir en contexto antes de pensar en RAG |
| Agenda | Mock en SQLite detrás de una interfaz. Office 365 (Graph) como adaptador opcional al final |
| **Harness de evaluación** | Clientes simulados con un LLM, a partir de los escenarios. Mide identificaciones correctas, **identificaciones falsas**, preguntas de más, citas bien reservadas y respuestas fieles a la base de conocimiento. Incluye **ruido de reconocimiento de voz simulado** en los nombres, y más adelante la latencia por turno |

## Stack (confirmado el 2026-10-01)

- **Python 3.12 con `uv`**, en un solo paquete. Todo es Python: datos, agente y evaluación.
- **LLM con tool use** detrás de una interfaz propia, con un adaptador por proveedor: el agente **no queda atado a ningún proveedor**. El modelo por defecto se elige con datos en F4 (el harness compara modelos y proveedores con los mismos escenarios); el coste de la demo pública lo paga Nicolás. La garantía de cero identificaciones falsas va en código, no en el modelo.
- **SQLite** para la BD sintética y la agenda.
- **pytest** desde el principio, sobre todo para la resolución de identidad.
- **Voz**: LiveKit Agents (ya lo exploró en el piloto) para el tiempo real y la telefonía SIP; STT y TTS por decidir (ElevenLabs lo usó en el piloto). Nicolás quiere probar las dos opciones, LiveKit y ElevenLabs. Se compara coste y latencia antes de elegir.
- **Demo pública y ejecutable**, no un vídeo: **llamada de voz desde el navegador** (WebRTC), sin número de teléfono real, con límite de uso para controlar el gasto. Un número real por SIP queda como opción para enseñarlo en directo.

## Fases

| | Fase | Nota |
|---|---|---|
| F1 | Generador sintético: BD estilo legacy + escenarios con verdad de referencia | ✅ Cerrada el 2026-10-01. Formato de escenarios en `docs/scenario-format.md` |
| F2 | Adaptador legacy + **resolución de identidad** con tests | El corazón, y se puede medir sin LLM |
| F3 | Agente con herramientas + base de conocimiento + agenda mock, por texto (CLI) | El número que llama se simula como parámetro |
| F4 | **Harness de evaluación** con clientes simulados | ⚠️ **Antes que la interfaz, a propósito** |
| F5 | **Capa de voz**: STT/TTS en tiempo real, latencia, interrupciones, castellano y catalán | El canal de verdad |
| F6 | Demo pública: llamada desde el navegador (y número SIP opcional) | La pieza que se enseña |
| F7 | README con métricas + case study en el portfolio | |

Bloques de 90 minutos. Es mejor cerrar una fase entera que dejar tres a medias.

## Política de identidad (decidida el 2026-10-01)

- Tres niveles: `none` → `probable` → `confirmed`.
- Se confirma con **el nombre más un factor que lo corrobore** (teléfono en ficha o nombre de mascota) y un único candidato. **El teléfono solo nunca confirma.**
- Un nombre que no coincide con nadie significa «no es cliente», diga lo que diga el teléfono.
- Quien no queda confirmado puede reservar una cita «sin verificar», marcada para recepción, sin leer ni escribir datos de ningún cliente. Cancelar y cambiar exigen `confirmed`.

## Convenciones

- Código, commits y README en **inglés**. Nicolás escribe en español.
- Commits pequeños con Conventional Commits.
- Cada fase cerrada deja una línea de estado en el README.
- Al final de cada sesión, un resumen corto en `docs/sessions/YYYY-MM-DD.md` (qué se hizo, qué funciona y qué sigue) para que Claudio lo registre en el vault.
