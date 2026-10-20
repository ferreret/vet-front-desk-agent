# Case study para el portfolio — vet-front-desk-agent

Texto para la ficha del proyecto en el portfolio (`data/projects/vet-front-desk-agent.ts`),
ordenado por los campos de `ProjectItem`. Castellano primero, inglés después. Todas las
cifras salen del README del repo y de `docs/evaluation.md`; ninguna es estimada salvo la
que lo dice.

**Requisito de Nicolás (2026-10-20):** la página tiene que decir que la demo es **una demo
reducida del proyecto completo**. Va en la descripción, en la solución y en el bloque
«Demo y código».

Falta, y no está aquí: la captura para `imageUrl` (la página de la demo) y, si se quiere,
un diagrama para `architectureDiagram` (el del README está en Mermaid).

---

## Datos comunes

| Campo | Valor |
|---|---|
| `slug` | `vet-front-desk-agent` |
| `title` | Vet Front Desk Agent |
| `link`, `demoUrl` | `https://demos.nicolasbarcelo.dev/clinica-veterinaria/` |
| `repoUrl` | `https://github.com/ferreret/vet-front-desk-agent` |
| `status` | `prototype` |
| `tags` | Python, AI Agents, Voice AI, LLM Evaluation |

**Métricas** (`businessMetrics`)

| ES | EN | Valor |
|---|---|---|
| Identificaciones falsas | False identifications | 0 |
| Llamadas simuladas al resolutor | Simulated calls to the resolver | 174.567 / 174,567 |
| Clientes identificados (de los que se podía) | Callers identified (of those who could be) | 97,7 % / 97.7% |
| Hasta las primeras palabras (mediana, modelo) | To the first words (median, model) | 1,2 s / 1.2 s |
| Coste del modelo por llamada | Model cost per call | 0,01 $ / $0.01 |
| Modelos comparados | Models compared | 10 (de 4 proveedores / from 4 providers) |

**Tecnologías** (`techStack`)

| ES | EN | Elementos |
|---|---|---|
| Agente | Agent | Python 3.12, Gemini 3.5 Flash Lite, tool use, adaptadores para Gemini, Claude y OpenAI / adapters for Gemini, Claude and OpenAI |
| Voz | Voice | LiveKit Agents, ElevenLabs Agents, WebRTC, SIP |
| Datos | Data | SQLite, generador sintético propio / own synthetic generator |
| Evaluación | Evaluation | pytest (685 tests), clientes simulados con LLM / LLM-simulated callers, Claude Opus como juez / as judge |
| Despliegue | Deployment | Docker, Dokploy (self-hosted), Cloudflare Tunnel, Traefik |

---

## Castellano

**`description`**

Centralita telefónica con IA para clínicas veterinarias: atiende la llamada por voz, averigua
quién llama y qué animales tiene en un programa de gestión antiguo, responde con la
información de la clínica y reserva, cambia o anula citas. Su regla principal: no revelar
nada a quien no ha identificado con pruebas suficientes. La demo pública, por el navegador,
es una versión reducida del proyecto completo.

**`role`**: Diseño, desarrollo y evaluación

**`timeline`**: Octubre de 2026 · tres semanas

**`problem`**

Los programas de gestión veterinaria son antiguos. En el tipo que modela este proyecto, cada
animal se relaciona con su dueño por el nombre del cliente, no por un código. Así que hay
homónimos, padres e hijos guardados con el mismo apellido, teléfonos que comparte toda la
familia o que cambiaron de manos hace años, y nombres que el reconocimiento de voz entiende
mal, sobre todo en catalán. Un agente ingenuo coge el primer resultado de la base de datos,
saluda a la persona equivocada y le cuenta la vacuna del perro de otro. Para un agente que
habla con clientes reales, la métrica que más importa no es cuántas veces acierta, sino
cuántas veces se equivoca de persona: tiene que ser cero.

**`solution`**

La identificación es una resolución de identidad con nivel de confianza, escrita en código
y sin modelo de lenguaje: con el número que llama, el nombre, una mascota y la población,
devuelve «confirmado», «pregunta esto» o «no es cliente», con la prueba de cada candidato.
El teléfono solo nunca confirma a nadie. Las herramientas que tocan datos de un cliente
fallan hasta que el resolutor lo ha confirmado: el modelo decide qué decir, no qué puede
saber. Alrededor hay un generador de clínicas sintéticas con los defectos de una real, y
un harness que juega cada escenario como una llamada entera, con un cliente simulado por un
LLM y errores de reconocimiento de voz simulados en los nombres. Con ese harness se eligió
el modelo entre diez de cuatro proveedores, y cada vez que algo dejado al modelo falló en
una ejecución medida, se llevó a código. La voz se probó por tres vías con el mismo agente
detrás (ElevenLabs Agents, Vapi y LiveKit con un programa propio), y el proyecto completo
atiende un número de teléfono real. La demo pública es una versión reducida, pensada para
no costar nada: el mismo agente con la misma barrera de privacidad, pero por el navegador,
solo en castellano, más lenta y sin lo que una clínica usaría detrás de la llamada.

**`lessonsLearned`**

- La garantía va en código, no en el prompt: un modelo más flojo y rápido enseñó los sitios
  donde la barrera de privacidad aún dependía de que el modelo obedeciera, y se arreglaron
  en código, no volviendo al modelo anterior.
- Un arreglo de prompt se mide antes de darlo por bueno: la instrucción de cómo decir las
  medias horas en catalán convirtió un fallo en seis (16:30 dicho como «les cinc i mitja»,
  las 17:30).
- Las pruebas solo cuentan en palabras de quien llama: el modelo rellenaba lo que el cliente
  no había contestado, así que la herramienta rechaza un nombre, una mascota o un motivo de
  visita que el cliente no haya dicho.
- Decirle al modelo qué hacer cuando algo no es urgente le movía el listón de qué lo es. La
  respuesta fue que el agente no haga triaje: quien pide el teléfono de urgencias lo recibe
  al momento, y a quien cuenta un síntoma se le ofrecen tres opciones y elige él.
- El harness llegó antes que la voz: 82 conversaciones con clientes simulados, por texto,
  cuestan en torno a 1 $ sin juez, así que cada cambio del agente se mide antes de oírlo
  por teléfono.
- Una demo pública gratuita es otro producto: con la voz gratuita de LiveKit el catalán
  sonaba mal y los turnos tardan más, así que la demo se quedó en castellano y dice que es
  una versión reducida.

**`content`** (bloque «Demo y código»)

```html
<h3>Demo y código</h3>
<p>La demo pública está en <a href="https://demos.nicolasbarcelo.dev/clinica-veterinaria/" target="_blank" rel="noopener noreferrer">demos.nicolasbarcelo.dev/clinica-veterinaria</a>: una llamada de voz desde el navegador en la que eliges quién eres (un cliente que llama desde su teléfono, uno con el número oculto, uno con el teléfono de otro, o alguien que no es cliente) e intentas que te cuente algo que no es tuyo.</p>
<p><strong>Es una demo reducida del proyecto completo.</strong> Funciona con capas gratuitas (LiveKit Cloud oye y habla, Gemini contesta), así que habla solo castellano, tarda entre 2,9 y 4,4 s por turno (1,7 a 2,2 s en el proyecto completo), dura tres minutos por llamada y deja fuera lo que una clínica usaría detrás: el número de teléfono, los avisos a recepción, el traspaso a una persona y una agenda que se guarda. El agente y su barrera de privacidad son los mismos. Si no contesta, se ha agotado el uso gratuito del mes.</p>
<p>El código, la evaluación y el registro de cada sesión están en <a href="https://github.com/ferreret/vet-front-desk-agent" target="_blank" rel="noopener noreferrer">GitHub</a>. Hecho con ayuda de IA (Claude Code); el diseño, la revisión y las decisiones son míos.</p>
```

---

## English

**`description`**

A voice AI front desk for veterinary clinics: it answers the call, works out who is calling
and which animals they have from an old practice-management system, answers from the
clinic's own information, and books, moves or cancels appointments. Its main rule: reveal
nothing to a caller it has not identified with enough evidence. The public browser demo is a
reduced version of the full project.

**`role`**: Design, development and evaluation

**`timeline`**: October 2026 · three weeks

**`problem`**

Veterinary practice-management systems are old. In the kind this project models, an animal
is linked to its owner by the owner's name, not by a key. So there are namesakes, parents
and children stored under one surname, phone numbers shared by a whole family or that
changed hands years ago, and names that speech recognition gets wrong, all the more in
Catalan. A naive agent takes the first database match, greets the wrong person and tells
them about someone else's dog's vaccine. For an agent that talks to real clients, the number
that matters most is not how often it gets the caller right, but how often it gets the wrong
person: that has to be zero.

**`solution`**

Identification is identity resolution with a confidence level, written in code with no
language model: from the calling number, a name, a pet and a town, it returns "confirmed",
"ask this" or "not a client", with the evidence for every candidate. A phone number alone
never confirms anybody. Tools that touch a client's data fail until the resolver has
confirmed the caller: the model decides what to say, not what it may know. Around it sit a
generator of synthetic clinics with the defects of a real one, and a harness that plays
every scenario as a whole call, with an LLM playing the caller and simulated speech
recognition errors in the names. That harness chose the model among ten from four
providers, and whenever something left to the model failed in a measured run, it was moved
into code. Voice was tried on three stacks with the same agent behind them (ElevenLabs
Agents, Vapi, and LiveKit with a program of our own), and the full project answers a real
phone number. The public demo is a reduced version, set up to cost nothing: the same agent
with the same privacy barrier, but in the browser, in Spanish only, slower, and without
what a clinic would use behind the call.

**`lessonsLearned`**

- The guarantee lives in code, not in the prompt: a weaker, faster model showed where the
  privacy barrier still depended on the model obeying, and those places were fixed in code,
  not by switching back.
- A prompt fix is measured before it is trusted: an instruction on how to say half hours in
  Catalan turned one failure into six (16:30 said as "les cinc i mitja", 17:30).
- Evidence counts only in the caller's own words: the model filled in what the caller had
  not answered, so the tool rejects a name, a pet or a reason for the visit the caller never
  said.
- Telling the model what to do when something is not urgent moved its line for what is. The
  answer was that the agent does not triage: whoever asks for the emergency number gets it
  at once, and whoever describes a symptom is offered three options and chooses.
- The harness came before the voice: 82 conversations with simulated callers, over text,
  cost about $1 without the judge, so every change to the agent is measured before it is
  heard on the phone.
- A free public demo is a different product: on LiveKit's free voice, Catalan sounded wrong
  and turns take longer, so the demo stays in Spanish and says it is a reduced version.

**`content`** ("Demo & code" block)

```html
<h3>Demo & code</h3>
<p>The public demo is at <a href="https://demos.nicolasbarcelo.dev/clinica-veterinaria/" target="_blank" rel="noopener noreferrer">demos.nicolasbarcelo.dev/clinica-veterinaria</a>: a voice call from your browser where you pick who you are (a client calling from their own phone, one with a hidden number, one on somebody else's phone, or somebody who is not a client) and try to get it to tell you something that is not yours.</p>
<p><strong>It is a reduced version of the full project.</strong> It runs on free tiers (LiveKit Cloud hears and speaks, Gemini answers), so it speaks Spanish only, takes 2.9 to 4.4 s a turn (1.7 to 2.2 s in the full project), lasts three minutes a call, and leaves out what a clinic would use behind it: the phone number, notes to reception, putting the call through to a person, and an appointment book that is kept. The agent and its privacy barrier are the same. If it does not answer, this month's free use has run out.</p>
<p>The code, the evaluation and a log of every session are on <a href="https://github.com/ferreret/vet-front-desk-agent" target="_blank" rel="noopener noreferrer">GitHub</a>. Built with AI assistance (Claude Code); design, review and decisions are mine.</p>
```
