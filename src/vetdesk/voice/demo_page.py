"""The public demo's page: who to call as, a button, and what is said.

One file with no build step, served by the voice server itself. It asks the server who a
visitor can call as and for a pass, and hands the call to the voice platform's own
browser library. Everything it shows of a caller is made up by this project.

When the server has other ways to carry a call set up, the page can offer the choice: the
same front desk, carried by one voice platform, by another, or by LiveKit with a program
of ours, to compare them. No key of any is in it. The choice is shown only to whoever
opens the page with `?via` in its address (`?via=vapi` or `?via=livekit` starts on that
one): the page is being tried by people who were sent it before there was a choice, and
they see it as it was.
"""

# The voice platforms' browser libraries, at the versions this page was written against.
SDK = "https://cdn.jsdelivr.net/npm/@elevenlabs/client@1.27.0/+esm"
VAPI_SDK = "https://cdn.jsdelivr.net/npm/@vapi-ai/web@2.7.1/+esm"
LIVEKIT_SDK = "https://cdn.jsdelivr.net/npm/livekit-client@2.22.3/+esm"

PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Planeta Animal — demo</title>
<style>
 :root{--ink:#1c2430;--soft:#5b6675;--line:#dfe3e8;--paper:#f7f8fa;--card:#fff;
   --accent:#0f6e5c;--accent-ink:#fff;--warn:#9a3412;--said:#eef6f4}
 @media (prefers-color-scheme: dark){:root{--ink:#e8ecf1;--soft:#9aa5b4;--line:#2c3542;
   --paper:#12171d;--card:#1a212a;--accent:#3fb39b;--accent-ink:#06231d;--warn:#fdba74;
   --said:#16302b}}
 *{box-sizing:border-box}
 body{margin:0;background:var(--paper);color:var(--ink);
   font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
 main{max-width:46rem;margin:0 auto;padding:1.5rem 1rem 4rem}
 header{display:flex;justify-content:space-between;align-items:baseline;gap:1rem}
 h1{font-size:1.5rem;margin:0}
 h2{font-size:1rem;margin:2rem 0 .6rem;color:var(--soft);font-weight:600;
   text-transform:uppercase;letter-spacing:.04em}
 p{margin:.5rem 0}
 .lead{font-size:1.1rem}
 .soft{color:var(--soft);font-size:.92rem}
 button{font:inherit;cursor:pointer}
 #lang{background:none;border:1px solid var(--line);color:var(--soft);border-radius:.4rem;
   padding:.2rem .6rem}
 #people{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))}
 .person{text-align:left;background:var(--card);color:inherit;border:2px solid var(--line);
   border-radius:.6rem;padding:.8rem .9rem}
 .person[aria-pressed=true]{border-color:var(--accent)}
 .person b{display:block}
 .person span{display:block;color:var(--soft);font-size:.9rem}
 #via{margin-top:1rem}
 #via span{color:var(--soft);margin-right:.5rem}
 .via{background:var(--card);color:inherit;border:2px solid var(--line);border-radius:.6rem;
   padding:.3rem .8rem;margin-right:.3rem}
 .via[aria-pressed=true]{border-color:var(--accent)}
 #sheet{background:var(--card);border:1px solid var(--line);border-radius:.6rem;
   padding:.9rem 1rem;margin-top:.8rem}
 #sheet dl{display:grid;grid-template-columns:auto 1fr;gap:.15rem 1rem;margin:.4rem 0 0}
 #sheet dt{color:var(--soft)}
 #sheet dd{margin:0;font-weight:600}
 #call{margin-top:1rem;background:var(--accent);color:var(--accent-ink);border:0;
   border-radius:.6rem;padding:.8rem 1.4rem;font-weight:600;font-size:1.05rem}
 #call:disabled{opacity:.5;cursor:default}
 #call.on{background:var(--warn);color:#fff}
 #state{margin-left:.8rem;color:var(--soft)}
 #said{list-style:none;margin:0;padding:0;display:grid;gap:.4rem}
 #said li{padding:.5rem .7rem;border-radius:.6rem;max-width:85%}
 #said .agent{background:var(--said)}
 #said .you{background:var(--card);border:1px solid var(--line);justify-self:end}
 .warn{color:var(--warn)}
 #decided{background:var(--card);border:1px solid var(--line);border-left:4px solid
   var(--accent);border-radius:.6rem;padding:.8rem 1rem}
 #decided pre{font:inherit;white-space:pre-wrap;margin:.5rem 0 0;padding:.6rem .8rem;
   background:var(--paper);border-radius:.5rem}
 footer{margin-top:2.5rem;border-top:1px solid var(--line);padding-top:1rem}
</style>
</head>
<body>
<main>
<header><h1>Planeta Animal</h1><button id="lang" type="button"></button></header>
<p class="lead" data-t="lead"></p>
<p class="soft" data-t="made_up"></p>

<h2 data-t="who"></h2>
<div id="people"></div>
<div id="sheet" hidden></div>
<p id="via" hidden><span data-t="via"></span><span id="platforms"></span></p>

<p><button id="call" type="button" disabled></button><span id="state" role="status"></span></p>
<p class="soft" id="left"></p>

<h2 data-t="said_title"></h2>
<ul id="said" aria-live="polite"></ul>

<div id="after" hidden>
<h2 data-t="decided_title"></h2>
<div id="decided"></div>
</div>

<footer class="soft">
<p data-t="kept"></p>
</footer>
</main>

<script type="module">
const TEXT = {
 es: {
  lead: "Una centralita con IA para una clínica veterinaria inventada. Llámala desde el "
      + "navegador: pide cita, pregunta un horario, o intenta que te cuente algo de otro cliente.",
  made_up: "Todo es inventado: la clínica, los clientes y sus animales. Elige quién eres; "
      + "el asistente no lo sabe, solo ve desde qué teléfono llamas.",
  who: "Quién llama", said_title: "Lo que se dice", via: "Vía de voz:",
  decided_title: "Lo que decidió el asistente",
  confirmed: name => `Te identificó como «${name}»`,
  by_phone: ", por tu nombre y por llamar desde el teléfono de esa ficha.",
  by_pet_town: ", por tu nombre, tu mascota y tu pueblo.",
  mistyped: " La ficha tiene el nombre mal escrito, y aun así te reconoció.",
  not_asked: "No preguntó quién eras: para lo que pediste no hacía falta.",
  not_a_client: "No te identificó: nadie en la clínica se llama así. Te atendió como a "
      + "alguien nuevo, sin abrir ninguna ficha.",
  other_phone: "No te identificó: llamabas desde el teléfono de la ficha de otra persona, "
      + "y desde ahí no confirma a nadie. Te atendió sin abrir ninguna ficha.",
  not_enough: "No te identificó: no llegó a tener pruebas suficientes de quién eras. Te "
      + "atendió sin abrir ninguna ficha.",
  reception: "Lo que habría recibido recepción. En una llamada por teléfono lo recibe de "
      + "verdad:",
  no_notice: "Recepción no habría recibido nada: no había nada que avisar.",
  call: "Llamar", hang: "Colgar", lang: "English",
  name: "Te llamas", town: "Vives en", pets: "Tus animales", phone: "Llamas desde",
  hidden: "número oculto", none: "ninguno en la clínica",
  booked: (pet, when) => `Tienes una cita puesta: ${pet}, ${when}. Prueba a cambiarla o a `
      + "anularla.",
  own: ["Cliente, desde su teléfono",
      "El número está en tu ficha. Con tu nombre debería bastar."],
  hidden_role: ["Cliente, con número oculto",
      "No llega ningún número. Te preguntará algo más antes de darte por identificado."],
  borrowed: ["Cliente, desde el teléfono de otro",
      "El número es de la ficha de otra persona. No te confirmará como nadie: cita sin "
      + "verificar."],
  stranger: ["No es cliente",
      "Tu nombre se parece mucho al de un cliente. No debería confundirte con él."],
  asking: "Pidiendo permiso…", mic: "Esperando el micrófono…", ringing: "Llamando…",
  listening: "Te escucha", speaking: "Habla el asistente", over: "Llamada terminada",
  left: (m, s) => `Quedan ${m} minutos de demo por hoy. `
      + `Cada llamada dura como mucho ${s / 60} minutos.`,
  full_day: "Se han acabado los minutos de demo de hoy. Vuelve mañana.",
  full_address: "Ya has hecho todas las llamadas de hoy desde esta conexión.",
  no_mic: "Sin micrófono no se puede llamar. Da permiso en el navegador y vuelve a intentarlo.",
  failed: "No se ha podido empezar la llamada. Inténtalo otra vez en un momento.",
  kept: "Lo que se dice en la llamada se guarda por escrito hasta noventa días para mejorar "
      + "el asistente. La voz no se graba.",
 },
 en: {
  lead: "An AI front desk for a made-up veterinary clinic. Call it from your browser: book "
      + "a visit, ask for the opening hours, or try to make it tell you about another client.",
  made_up: "Everything is made up: the clinic, its clients and their animals. Pick who you "
      + "are; the assistant does not know, it only sees which phone you call from.",
  who: "Who is calling", said_title: "What is said", via: "Voice platform:",
  decided_title: "What the assistant decided",
  confirmed: name => `It identified you as "${name}"`,
  by_phone: ", by your name and by the call coming from the phone on that record.",
  by_pet_town: ", by your name, your pet and your town.",
  mistyped: " The name is misspelt on the record, and it knew you all the same.",
  not_asked: "It did not ask who you were: what you asked for did not need it.",
  not_a_client: "It did not identify you: nobody at the clinic has that name. It served "
      + "you as somebody new, without opening any record.",
  other_phone: "It did not identify you: you were calling from the phone on another "
      + "person's record, and from there it confirms nobody. It served you without "
      + "opening any record.",
  not_enough: "It did not identify you: it never had enough proof of who you were. It "
      + "served you without opening any record.",
  reception: "What reception would have been told, in Spanish as the clinic reads it. On "
      + "a phone call it is really sent:",
  no_notice: "Reception would have been told nothing: there was nothing to tell.",
  call: "Call", hang: "Hang up", lang: "Español",
  name: "Your name", town: "You live in", pets: "Your animals", phone: "You call from",
  hidden: "a hidden number", none: "none at the clinic",
  booked: (pet, when) => `You have a visit booked: ${pet}, ${when}. Try moving it or `
      + "cancelling it.",
  own: ["A client, from their own phone",
      "The number is on your record. Your name should be enough."],
  hidden_role: ["A client, from a hidden number",
      "No number arrives. It will ask for something more before it takes you as identified."],
  borrowed: ["A client, from somebody else's phone",
      "The number is on another person's record. It will confirm you as nobody: an "
      + "unverified visit."],
  stranger: ["Not a client",
      "Your name is very like a client's. It should not take you for them."],
  asking: "Asking for a pass…", mic: "Waiting for the microphone…", ringing: "Calling…",
  listening: "Listening", speaking: "The assistant is speaking", over: "The call is over",
  left: (m, s) => `${m} minutes of demo left today. A call lasts ${s / 60} minutes at most.`,
  full_day: "Today's demo minutes are used up. Come back tomorrow.",
  full_address: "You have made all of today's calls from this connection.",
  no_mic: "No call without a microphone. Allow it in the browser and try again.",
  failed: "The call could not be started. Try again in a moment.",
  kept: "What is said on the call is kept in writing for up to ninety days, to improve the "
      + "assistant. The voice is not recorded.",
 },
};
const ROLE = {own: "own", hidden: "hidden_role", borrowed: "borrowed", stranger: "stranger"};
const PLATFORM = {elevenlabs: "ElevenLabs", vapi: "Vapi", livekit: "LiveKit"};
const $ = id => document.getElementById(id);
const speaks = navigator.language || "es";
let lang = speaks.startsWith("es") || speaks.startsWith("ca") ? "es" : "en";
let people = [], chosen = null, conversation = null, timer = null, info = null;
let pass = null, result = null, platform = "elevenlabs";
// The choice of voice platform, for whoever opens the page asking for it: see above.
const via = new URLSearchParams(location.search).get("via");
const t = key => TEXT[lang][key];

function state(text, warn = false) {
  $("state").textContent = text;
  $("state").className = warn ? "warn" : "";
}

function line(who, text) {
  if (!text) return;
  const item = document.createElement("li");
  item.className = who;
  item.textContent = text;  // as text, never as markup
  $("said").append(item);
  item.scrollIntoView({block: "nearest"});
  return item;
}

function sheet() {
  const person = people.find(p => p.key === chosen);
  $("sheet").hidden = !person;
  if (!person) return;
  const rows = [[t("name"), person.name], [t("town"), person.town],
                [t("pets"), person.pets.join(", ") || t("none")],
                [t("phone"), person.phone || t("hidden")]];
  const list = document.createElement("dl");
  for (const [label, value] of rows) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = value;
    list.append(dt, dd);
  }
  const what = document.createElement("p");
  what.className = "soft";
  what.textContent = t(ROLE[person.key])[1];
  $("sheet").replaceChildren(what, list);
  if (person.appointment) {
    const visit = document.createElement("p");
    visit.textContent = t("booked")(person.appointment.pet, person.appointment[lang]);
    $("sheet").append(visit);
  }
}

function decided() {
  $("after").hidden = !result;
  if (!result) return;
  const who = document.createElement("p"), told = document.createElement("p");
  const identity = result.identity;
  who.textContent = identity.level === "confirmed"
      ? t("confirmed")(identity.name) + t(identity.by === "phone" ? "by_phone" : "by_pet_town")
        + (identity.mistyped ? t("mistyped") : "")
      : t(identity.why);
  told.className = "soft";
  told.textContent = t(result.reception.length ? "reception" : "no_notice");
  const notices = result.reception.map(text => {
    const notice = document.createElement("pre");
    notice.textContent = text;  // as text, never as markup
    return notice;
  });
  $("decided").replaceChildren(who, told, ...notices);
}

async function ask_what_happened() {
  // The last answer's notices are written down as its turn ends, a moment after it is said.
  await new Promise(done => setTimeout(done, 800));
  try {
    const answer = await fetch("demo/result?pass=" + encodeURIComponent(pass));
    result = answer.ok ? await answer.json() : null;
  } catch { result = null; }
  decided();
}

function draw() {
  document.documentElement.lang = lang;
  for (const node of document.querySelectorAll("[data-t]")) {
    node.textContent = t(node.dataset.t);
  }
  $("lang").textContent = t("lang");
  $("call").textContent = conversation ? t("hang") : t("call");
  $("call").classList.toggle("on", !!conversation);
  $("people").replaceChildren(...people.map(person => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "person";
    button.setAttribute("aria-pressed", person.key === chosen);
    const title = document.createElement("b"), who = document.createElement("span");
    title.textContent = t(ROLE[person.key])[0]; who.textContent = person.name;
    button.append(title, who);
    button.onclick = () => { if (!conversation) { chosen = person.key; draw(); } };
    return button;
  }));
  sheet();
  const offered = (via !== null && info && info.platforms) || [];
  $("via").hidden = offered.length < 2;
  $("platforms").replaceChildren(...offered.map(name => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "via";
    button.setAttribute("aria-pressed", name === platform);
    button.textContent = PLATFORM[name] || name;
    button.onclick = () => { if (!conversation) { platform = name; draw(); } };
    return button;
  }));
  if (info) $("left").textContent = t("left")(info.minutes_left, info.seconds_a_call);
  $("call").disabled = !chosen;
  decided();
}

async function load() {
  const first = !info;
  info = await (await fetch("demo/people")).json();
  people = info.people;
  if (first && (info.platforms || []).includes(via)) platform = via;
  chosen = chosen || (people[0] && people[0].key);
  draw();
}

function ended() {
  clearTimeout(timer);
  conversation = null;
  state(t("over"));
  draw();
  load();
  if (pass) ask_what_happened();
}

async function by_elevenlabs(given) {
  const {Conversation} = await import("__SDK__");
  return await Conversation.startSession({
    signedUrl: given.signed_url,
    dynamicVariables: {demo_pass: given.pass},
    onMessage: ({message, role}) => line(role === "user" ? "you" : "agent", message),
    onModeChange: ({mode}) => state(t(mode === "speaking" ? "speaking" : "listening")),
    onDisconnect: ended,
    onError: () => state(t("failed"), true),
  });
}

async function by_vapi(given) {
  const library = await import("__VAPI_SDK__");
  const Vapi = library.default.default || library.default;
  // The library is told to ask this server, not the platform, to start the call, and is
  // given the pass where a key of the platform's would go: there is none in this page.
  const vapi = new Vapi(given.pass, new URL("demo/vapi", location.href).href);
  vapi.on("message", said => {
    if (said.type === "transcript" && said.transcriptType === "final") {
      line(said.role === "user" ? "you" : "agent", said.transcript);
    }
  });
  // The call is over once, whoever ends it: the platform, the server or the button.
  let over = false;
  const end = () => { if (!over) { over = true; ended(); } };
  vapi.on("speech-start", () => over || state(t("speaking")));
  vapi.on("speech-end", () => over || state(t("listening")));
  vapi.on("call-end", end);
  vapi.on("error", () => state(t("failed"), true));
  if (!await vapi.start("demo")) throw new Error("no call");
  return over ? null : {endSession: async () => { await vapi.stop(); end(); }};
}

async function by_livekit(given) {
  const {Room, RoomEvent} = await import("__LIVEKIT_SDK__");
  // A room of this call's own, and a pass to it signed by this server: no key is here.
  const room = new Room();
  const written = new Map(), playing = [];
  let over = false;
  const end = () => {
    if (over) return;
    over = true;
    playing.forEach(player => player.remove());
    ended();
  };
  // What is said arrives as text, a phrase at a time and more than once while it is
  // being heard: each phrase is written once and corrected in place.
  room.registerTextStreamHandler("lk.transcription", async (reader, who) => {
    let text;
    try { text = await reader.readAll(); } catch { return; }
    const phrase = reader.info.attributes["lk.segment_id"] || reader.info.id;
    const mine = who.identity === room.localParticipant.identity;
    if (written.has(phrase)) written.get(phrase).textContent = text;
    else if (text) written.set(phrase, line(mine ? "you" : "agent", text));
  });
  room.on(RoomEvent.TrackSubscribed, track => {
    if (track.kind !== "audio") return;
    const player = track.attach();
    document.body.append(player);
    playing.push(player);
  });
  room.on(RoomEvent.ParticipantAttributesChanged, changed => {
    const doing = changed["lk.agent.state"];
    if (doing && !over) state(t(doing === "speaking" ? "speaking" : "listening"));
  });
  room.on(RoomEvent.Disconnected, end);
  await room.connect(given.url, given.token);
  await room.localParticipant.setMicrophoneEnabled(true);
  return over ? null : {endSession: async () => { await room.disconnect(); end(); }};
}

const BY = {elevenlabs: by_elevenlabs, vapi: by_vapi, livekit: by_livekit};

async function call() {
  if (conversation) { await conversation.endSession(); return; }
  $("call").disabled = true;
  $("said").replaceChildren();
  pass = result = null;
  decided();
  try {
    state(t("mic"));
    try {
      const heard = await navigator.mediaDevices.getUserMedia({audio: true});
      heard.getTracks().forEach(track => track.stop());
    } catch { state(t("no_mic"), true); return; }
    state(t("asking"));
    const answer = await fetch("demo/call", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({as: chosen, platform})});
    if (answer.status === 429) {
      state(t((await answer.json()).why === "address" ? "full_address" : "full_day"), true);
      return;
    }
    if (!answer.ok) { state(t("failed"), true); return; }
    const given = await answer.json();
    pass = given.pass;
    state(t("ringing"));
    conversation = await BY[platform](given);
    if (!conversation) return;  // over before it began: it has been said so already
    // The server closes the call when its time is up; this is for a page it cannot reach.
    timer = setTimeout(() => conversation && conversation.endSession(),
                       (given.seconds + 25) * 1000);
    state(t("listening"));
  } catch {
    state(t("failed"), true);
    conversation = null;
  } finally {
    draw();
  }
}

$("call").onclick = call;
$("lang").onclick = () => { lang = lang === "es" ? "en" : "es"; draw(); };
draw();
load().catch(() => state(t("failed"), true));
</script>
</body>
</html>
""".replace("__SDK__", SDK).replace("__VAPI_SDK__", VAPI_SDK).replace(
    "__LIVEKIT_SDK__", LIVEKIT_SDK)
