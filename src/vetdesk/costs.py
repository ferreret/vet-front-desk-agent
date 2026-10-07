"""What a call costs, from what the calls taken so far were billed.

Two bills run while the front desk is on the phone, and each keeps its own record:

- the voice platform (ears, voice and line), which says for every conversation how long
  it lasted and what it charged for it;
- the model, whose tokens the server writes down turn by turn in its call log, priced
  from the list in `evals.cost`.

This reads both, matches them call by call, and writes out what one call and one minute
cost and what a month of calls would. Nothing here is an estimate of anybody's price
list: the figures are what was charged, and what was not measured is said to be missing.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ELEVENLABS = "https://api.elevenlabs.io/v1"
# How the platform names where a conversation came from, and how the report does.
SOURCES = {"sip_trunk": "teléfono", "twilio": "teléfono", "react_sdk": "navegador",
           "js_sdk": "navegador", "widget": "navegador"}
OTHER = "otras (pruebas escritas)"

Get = Callable[[str, dict[str, str]], dict]


def _get(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


@dataclass(frozen=True)
class VoiceCall:
    """What the voice platform says of one conversation."""

    id: str
    started: datetime
    seconds: int
    credits: int
    dollars: float
    source: str
    tier: str = ""


@dataclass(frozen=True)
class ModelCall:
    """What the server wrote down of one call: the model's side of the bill."""

    id: str
    turns: int
    requests: int
    tokens_in: int
    tokens_out: int
    dollars: float
    seconds: int = 0
    taken_back: int = 0


def voice_calls(key: str, agent: str, cache: Path | None = None,
                get: Get = _get) -> list[VoiceCall]:
    """Every conversation the agent has had on the platform, with what each was charged.

    The charge is in each conversation's own record, one request apiece. A conversation
    that is over does not change, so what was read once is kept in `cache`.
    """
    headers = {"xi-api-key": key}
    known = json.loads(cache.read_text(encoding="utf-8")) if cache and cache.exists() else {}
    cursor, listed = "", []
    while True:
        query = {"agent_id": agent, "page_size": "100", **({"cursor": cursor} if cursor else {})}
        page = get(f"{ELEVENLABS}/convai/conversations?{urllib.parse.urlencode(query)}", headers)
        listed += page.get("conversations", [])
        cursor = page.get("next_cursor") or ""
        if not page.get("has_more") or not cursor:
            break
    for conversation in listed:
        name = conversation["conversation_id"]
        if name in known or conversation.get("status") not in (None, "done", "failed"):
            continue  # read before, or still going on
        metadata = get(f"{ELEVENLABS}/convai/conversations/{name}", headers)["metadata"]
        charging = metadata.get("charging") or {}
        known[name] = {
            "started": metadata.get("start_time_unix_secs"),
            "seconds": metadata.get("call_duration_secs") or 0,
            "credits": metadata.get("cost") or 0,
            "dollars": (charging.get("platform_price") or 0) + (charging.get("llm_price") or 0),
            "source": metadata.get("conversation_initiation_source") or "",
            "tier": charging.get("tier") or "",
        }
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, indent=1), encoding="utf-8")
    wanted = {conversation["conversation_id"] for conversation in listed}
    calls = [VoiceCall(name, datetime.fromtimestamp(row["started"] or 0), row["seconds"],
                       row["credits"], row["dollars"], row["source"], row["tier"])
             for name, row in known.items() if name in wanted]
    return sorted(calls, key=lambda call: call.started)


def model_calls(server: str, admin_key: str, get: Get = _get) -> list[ModelCall]:
    """Every call in the server's log, with the tokens and the money the model took."""
    listed = get(server.rstrip("/") + "/calls?limit=100000",
                 {"Authorization": f"Bearer {admin_key}"})
    return [ModelCall(call["id"], call["turns"], call["requests"], call["tokens_in"],
                      call["tokens_out"], call["dollars"], call["seconds"], call["taken_back"])
            for call in listed["calls"] if call["turns"]]


# --- the report -------------------------------------------------------------------------------

def _n(value: float, places: int = 2) -> str:
    """A number as it is written in Spanish: 1.234,56."""
    whole, _, rest = f"{value:,.{places}f}".partition(".")
    return whole.replace(",", ".") + ("," + rest if rest else "")


def _usd(value: float, places: int = 2) -> str:
    return _n(value, places) + " $"


def _table(head: Iterable[str], rows: Iterable[Iterable[str]]) -> list[str]:
    head = list(head)
    lines = ["| " + " | ".join(head) + " |",
             "|" + "|".join(["---"] + ["---:"] * (len(head) - 1)) + "|"]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def report(voice: list[VoiceCall], model: list[ModelCall], today: datetime, *,
           model_name: str = "", calls_per_month: tuple[int, ...] = (300, 1000, 3000),
           minutes: float | None = None, phone_per_minute: float | None = None,
           fixed_per_month: float | None = None, fixed_is: str = "") -> str:
    """The report, in Markdown and in Spanish: it is for whoever decides on the money."""
    out = [f"# Lo que cuesta la centralita — {today:%d/%m/%Y}", ""]
    spoken = [call for call in voice if call.seconds > 0]
    if not spoken:
        return "\n".join(out + ["Todavía no hay ninguna llamada de la que la plataforma de "
                                "voz haya cobrado nada."]) + "\n"
    voice_minutes = sum(call.seconds for call in spoken) / 60
    voice_dollars = sum(call.dollars for call in spoken)
    per_minute = voice_dollars / voice_minutes
    by_id = {call.id: call for call in spoken}
    both = [(by_id[call.id], call) for call in model if call.id in by_id]
    only_model = [call for call in model if call.id not in by_id]
    first = min(call.started for call in spoken)
    last = max(call.started for call in spoken)
    tiers = sorted({call.tier for call in spoken if call.tier})

    out += ["Las cifras son lo que se ha cobrado por las llamadas hechas hasta hoy, no una "
            "tarifa de catálogo. Todo en dólares, que es en lo que facturan los dos "
            "proveedores.", "",
            "## Lo medido", "",
            f"Del {first:%d/%m/%Y} al {last:%d/%m/%Y}.", ""]
    rows = []
    for label in (*dict.fromkeys(SOURCES.values()), OTHER):
        part = [call for call in spoken if SOURCES.get(call.source, OTHER) == label]
        if part:
            part_minutes = sum(call.seconds for call in part) / 60
            part_dollars = sum(call.dollars for call in part)
            rows.append((f"Voz: {label}", str(len(part)), _n(part_minutes, 1),
                         _usd(part_dollars), _usd(part_dollars / len(part), 3),
                         _usd(part_dollars / part_minutes, 4)))
    rows.append(("**Voz: todas**", str(len(spoken)), _n(voice_minutes, 1), _usd(voice_dollars),
                 _usd(voice_dollars / len(spoken), 3), _usd(per_minute, 4)))
    model_per_minute, typed_only = None, False
    if model:
        # The model's price per minute comes from calls somebody spoke on. Typed to, the
        # agent's turns follow one another faster than anybody talks, and a minute of
        # that holds more of them. With nothing but typed calls it is said so.
        talked = [call for voice_call, call in both if voice_call.source in SOURCES]
        typed_only = not talked
        counted = talked or model
        model_dollars = sum(call.dollars for call in counted)
        # Minutes as the voice platform counts them where both know the call; the server
        # only sees from the first line to the last.
        model_minutes = sum((by_id[call.id].seconds if call.id in by_id else call.seconds)
                            for call in counted) / 60
        model_per_minute = model_dollars / model_minutes if model_minutes else None
        rows.append((f"**Modelo** ({model_name or 'el del agente'})", str(len(counted)),
                     _n(model_minutes, 1), _usd(model_dollars, 4),
                     _usd(model_dollars / len(counted), 4),
                     _usd(model_per_minute, 4) if model_per_minute else "—"))
    out += _table(("", "Llamadas", "Minutos", "Cobrado", "Por llamada", "Por minuto"), rows)
    out += ["",
            "- **Voz** es lo que cobra la plataforma de voz por oír, hablar y llevar la "
            f"llamada: {_n(sum(call.credits for call in spoken), 0)} créditos en total"
            + (f", al precio del plan «{', '.join(tiers)}»" if tiers else "")
            + ". El plan trae créditos cada mes; los dólares son lo que valen los gastados.",
            "- **Modelo** es lo que cuesta el modelo de lenguaje que decide qué contestar, "
            "por tokens, a precio de lista."]
    if model:
        tokens_in = sum(call.tokens_in for call in model)
        tokens_out = sum(call.tokens_out for call in model)
        turns = sum(call.turns for call in model)
        back = sum(call.taken_back for call in model)
        out.append(f"  {_n(tokens_in, 0)} tokens de entrada y {_n(tokens_out, 0)} de salida en "
                   f"{turns} turnos"
                   + (f"; {back} de ellos son respuestas que no se llegaron a oír porque la "
                      "frase llegó otra vez, y se pagan igual" if back else "") + ".")
        if typed_only:
            out.append("- **El servidor solo tiene apuntadas pruebas escritas**, en las que "
                       "los turnos van más seguidos que hablando: el precio del modelo por "
                       "minuto sale más alto de lo que será por teléfono. Se corrige solo "
                       "en cuanto haya llamadas habladas.")
        elif len(counted) < len(model):
            out.append(f"- El precio del modelo sale de las llamadas habladas ({len(counted)}); "
                       f"las pruebas escritas ({len(model) - len(counted)}) no cuentan para "
                       "él, porque en ellas los turnos van más seguidos que hablando.")
        if len(both) < len(spoken):
            out.append("- El servidor apunta el gasto del modelo desde hace poco: llamadas "
                       f"en su registro, {len(model)}; en la plataforma de voz, {len(spoken)}.")
        if only_model:
            out.append("- Llamadas del servidor que no están en la plataforma de voz "
                       f"(pruebas hechas sin ella): {len(only_model)}.")
    else:
        out.append("  **Aún no hay ninguna llamada con el gasto del modelo apuntado**: el "
                   "servidor lo registra desde que se desplegó el registro de llamadas. "
                   "Hasta que haya alguna, este informe solo puede dar la parte de voz.")

    mean = minutes if minutes is not None else voice_minutes / len(spoken)
    out += ["", "## Lo que cuesta una llamada", "",
            f"Una llamada de {_n(mean, 1)} minutos"
            + (" (la duración pedida)" if minutes is not None
               else " (la media de las hechas hasta hoy)") + ":", ""]
    parts = [("Voz", per_minute * mean)]
    if model_per_minute is not None:
        parts.append(("Modelo", model_per_minute * mean))
    if phone_per_minute is not None:
        parts.append(("Línea telefónica", phone_per_minute * mean))
    one_call = sum(amount for _, amount in parts)
    out += _table(("", "Por llamada", "Por minuto"),
                  [*((label, _usd(amount, 4), _usd(amount / mean, 4)) for label, amount in parts),
                   ("**Total**", f"**{_usd(one_call, 4)}**", f"**{_usd(one_call / mean, 4)}**")])
    if model_per_minute is not None:
        out += ["", f"De cada dólar, {_n(100 * per_minute * mean / one_call, 0)} céntimos son "
                    "de voz."]

    out += ["", "## Lo que costaría un mes", "",
            f"Con llamadas de {_n(mean, 1)} minutos:", ""]
    if fixed_per_month is None:
        out += _table(("Llamadas al mes", "Minutos", "Coste al mes"),
                      [(_n(count, 0), _n(count * mean, 0), f"**{_usd(count * one_call)}**")
                       for count in calls_per_month])
    else:
        out += _table(("Llamadas al mes", "Minutos", "Por las llamadas", "Fijo", "Total al mes"),
                      [(_n(count, 0), _n(count * mean, 0), _usd(count * one_call),
                        _usd(fixed_per_month),
                        f"**{_usd(count * one_call + fixed_per_month)}**")
                       for count in calls_per_month])
        if fixed_is:  # a figure nobody can check says what it is made of
            out += ["", f"El fijo es: {fixed_is}"]

    out += ["", "## Lo que no entra en estas cifras", ""]
    missing = []
    if model_per_minute is None:
        missing.append("**El modelo**, hasta que el servidor tenga llamadas apuntadas.")
    if phone_per_minute is None:
        missing.append("**La línea telefónica** (el número y lo que cobre el operador por "
                       "minuto): no se ha medido. Se puede añadir con `--phone-per-minute`.")
    if fixed_per_month is None:
        missing.append("**Los gastos fijos**: el servidor y la cuota de la línea. Se pueden "
                       "añadir con `--fixed-per-month`.")
    missing += [
        "La cuota del plan de la plataforma de voz no se suma a los fijos: los créditos "
        "gastados ya están contados a su precio. Es un mínimo, que se paga aunque no se "
        "gasten.",
        "El precio de voz es el del plan contratado hoy; un plan mayor baja el precio por "
        "minuto y uno menor lo sube.",
        "Los avisos por Telegram y el calendario no cuestan nada.",
        "Lo gastado en desarrollar y medir el agente (las tandas de evaluación) es otro "
        "gasto, que no se repite con cada llamada.",
        "Las llamadas medidas son de prueba y cortas. Una clínica de verdad tendrá llamadas "
        "más largas: `--minutes` rehace las cuentas con otra duración.",
    ]
    return "\n".join(out + [f"- {line}" for line in missing]) + "\n"
