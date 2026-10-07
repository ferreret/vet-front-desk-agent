"""A record of every call the front desk takes: what was said, what was done, what it cost.

The server's own log said all of this, as text that a deployment throws away. Here it is
kept, in a database of its own on the server's disk, so that somebody at the clinic can
read a call back (who rang, what they wanted, what the agent did about it) and so that
what the model costs is known call by call instead of guessed.

Kept for each call: when it began, the number it came from, the language it went on in,
how far the caller was identified, and what reception was told about it. For each line:
the caller's words as the recogniser wrote them, the agent's answer, the tools it ran, how
long the model took, and the tokens and the money. An answer that was taken back, because
the recogniser handed the same line over again written differently, is kept and marked: it
was not heard, but it was paid for.

It is written off the call's path, on a worker thread: a full disk makes no caller wait.
Calls older than `keep_days` are dropped when the server starts: a record of what clients
said on the phone is not something to keep for ever.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..agent.agent import Turn
from ..dbguard import APPLICATION_ID, ForeignDatabaseError, is_generated_db

log = logging.getLogger("vetdesk.calls")

FILE, KEEP_DAYS = "VETDESK_CALLS", "VETDESK_CALLS_KEEP_DAYS"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id        TEXT PRIMARY KEY,
    started   TEXT NOT NULL,
    caller    TEXT,
    model     TEXT,
    language  TEXT,
    identity  TEXT NOT NULL DEFAULT 'none',
    client    TEXT,
    happened  TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS lines (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id      TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    at           TEXT NOT NULL,
    turn         INTEGER,
    heard        TEXT,
    said         TEXT,
    note         TEXT,
    tools        TEXT NOT NULL DEFAULT '[]',
    first_words  REAL,
    seconds      REAL,
    requests     INTEGER NOT NULL DEFAULT 0,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_cached INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    dollars      REAL,
    taken_back   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS lines_of_call ON lines(call_id, seq);
"""
_SUMS = ("COUNT(l.turn) AS turns, COALESCE(SUM(l.requests), 0) AS requests, "
         "COALESCE(SUM(l.tokens_in), 0) AS tokens_in, "
         "COALESCE(SUM(l.tokens_cached), 0) AS tokens_cached, "
         "COALESCE(SUM(l.tokens_out), 0) AS tokens_out, "
         "COALESCE(SUM(l.dollars), 0) AS dollars, COALESCE(SUM(l.taken_back), 0) AS taken_back, "
         "MAX(l.at) AS last")


class CallLog:
    def __init__(self, path: Path | None = None, now: Callable[[], datetime] = datetime.now,
                 keep_days: int = 90) -> None:
        self._now = now
        if path is not None and path.exists() and path.stat().st_size \
                and not is_generated_db(path):
            # Not a file of ours: somebody's data. Never opened, never written to.
            raise ForeignDatabaseError(f"{path} is not a call log this project made")
        self._db = sqlite3.connect(":memory:" if path is None else path,
                                   check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        old = (now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
        dropped = self._db.execute("DELETE FROM calls WHERE started < ?", (old,)).rowcount
        if dropped:
            log.info("%d calls from more than %d days ago were dropped", dropped, keep_days)
        self._lock = threading.Lock()  # one connection, the writer and whoever reads
        self._jobs: queue.Queue[tuple[str, tuple]] = queue.Queue()
        threading.Thread(target=self._work, daemon=True, name="call-log").start()

    # --- writing: queued, never waited for -------------------------------------------------

    def _write(self, sql: str, *values: Any) -> None:
        self._jobs.put((sql, values))

    def _work(self) -> None:
        while True:
            sql, values = self._jobs.get()
            try:
                with self._lock:
                    self._db.execute(sql, values)
            except Exception as error:  # the record is not worth a call
                log.warning("a line of the call log was not written: %s", error)
            finally:
                self._jobs.task_done()

    def wait(self) -> None:
        """Until everything queued is written: for tests, and before reading back."""
        self._jobs.join()

    def _at(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def begin(self, call: str, caller: str | None, model: str, greeting: str = "") -> None:
        """A call that has just come in. Told again about the same call, nothing happens."""
        self._write("INSERT OR IGNORE INTO calls (id, started, caller, model) "
                    "VALUES (?, ?, ?, ?)", call, self._at(), caller, model)
        if greeting:
            # Once per call: the row above was either written just now or was there already.
            self._write("INSERT INTO lines (call_id, at, said, note) SELECT ?, ?, ?, 'greeting' "
                        "WHERE NOT EXISTS (SELECT 1 FROM lines WHERE call_id = ?)",
                        call, self._at(), greeting, call)

    def turn(self, call: str, number: int | None, heard: str, turn: Turn,
             dollars: float | None, language: str, identity: str = "none",
             client: str | None = None, note: str | None = None) -> None:
        """A line of the caller's and the agent's answer to it, with what the answer took."""
        usage = turn.usage
        tools = [{"name": event.name, "arguments": event.arguments, "ok": not event.is_error}
                 for event in turn.events]
        self._write(
            "INSERT INTO lines (call_id, at, turn, heard, said, note, tools, first_words, "
            "seconds, requests, tokens_in, tokens_cached, tokens_out, dollars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            call, self._at(), number, heard, turn.text, note,
            json.dumps(tools, ensure_ascii=False, default=str), turn.first_words,
            turn.seconds, turn.requests,
            usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
            usage.cache_read_tokens, usage.output_tokens, dollars)
        self._write("UPDATE calls SET language = ?, identity = ?, client = ? WHERE id = ?",
                    language, identity, client, call)

    def said(self, call: str, text: str, note: str, heard: str | None = None) -> None:
        """Something said with no model behind it: a stock phrase after a silence."""
        self._write("INSERT INTO lines (call_id, at, heard, said, note) VALUES (?, ?, ?, ?, ?)",
                    call, self._at(), heard, text, note)

    def note(self, call: str, what: str) -> None:
        """Something done about the call that nobody said: the platform told to hang up."""
        self._write("INSERT INTO lines (call_id, at, note) VALUES (?, ?, ?)",
                    call, self._at(), what)

    def taken_back(self, call: str, number: int) -> None:
        """The answer to this turn was not heard: the line came again, written differently."""
        self._write("UPDATE lines SET taken_back = 1 WHERE call_id = ? AND turn = ?",
                    call, number)

    def happened(self, call: str, kind: str) -> None:
        """One more thing reception was told about this call: see `notices`."""
        self._write("UPDATE calls SET happened = json_insert(happened, '$[#]', ?) WHERE id = ?",
                    kind, call)

    # --- reading ---------------------------------------------------------------------------

    def _rows(self, sql: str, *values: Any) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, values).fetchall()]

    @staticmethod
    def _call(row: dict) -> dict:
        row["happened"] = json.loads(row["happened"])
        started, last = (datetime.fromisoformat(row[name]) if row[name] else None
                         for name in ("started", "last"))
        row["seconds"] = int((last - started).total_seconds()) if started and last else 0
        del row["last"]
        return row

    def calls(self, limit: int = 200) -> list[dict]:
        """The latest calls, newest first, each with what it added up to."""
        rows = self._rows(
            f"SELECT c.*, {_SUMS} FROM calls c LEFT JOIN lines l ON l.call_id = c.id "
            "GROUP BY c.id ORDER BY c.started DESC, c.rowid DESC LIMIT ?", limit)
        return [self._call(row) for row in rows]

    def call(self, call: str) -> dict | None:
        """One call, with every line of it in the order it happened."""
        rows = self._rows(
            f"SELECT c.*, {_SUMS} FROM calls c LEFT JOIN lines l ON l.call_id = c.id "
            "WHERE c.id = ? GROUP BY c.id", call)
        if not rows:
            return None
        found = self._call(rows[0])
        found["lines"] = self._rows(
            "SELECT at, turn, heard, said, note, tools, first_words, seconds, requests, "
            "tokens_in, tokens_cached, tokens_out, dollars, taken_back FROM lines "
            "WHERE call_id = ? ORDER BY seq", call)
        for line in found["lines"]:
            line["tools"] = json.loads(line["tools"])
        return found

    def totals(self) -> dict:
        """Everything on record, added up."""
        (row,) = self._rows(
            "SELECT (SELECT COUNT(*) FROM calls) AS calls, COUNT(l.turn) AS turns, "
            "COALESCE(SUM(l.requests), 0) AS requests, "
            "COALESCE(SUM(l.tokens_in), 0) AS tokens_in, "
            "COALESCE(SUM(l.tokens_cached), 0) AS tokens_cached, "
            "COALESCE(SUM(l.tokens_out), 0) AS tokens_out, "
            "COALESCE(SUM(l.dollars), 0) AS dollars FROM lines l")
        return row


PAGE = """<!doctype html>
<html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Llamadas</title>
<style>
 body{font:16px/1.5 system-ui,sans-serif;max-width:68rem;margin:2rem auto;padding:0 1rem;
      color:#1c1c1c}
 input,button{font:inherit;padding:.4rem .7rem}
 #said{margin:.8rem 0;color:#b00020}
 #totals{display:flex;flex-wrap:wrap;gap:.75rem;margin:1rem 0}
 #totals div{background:#f3f4f6;border-radius:.5rem;padding:.6rem .9rem;min-width:8rem}
 #totals b{display:block;font-size:1.3rem}
 table{border-collapse:collapse;width:100%;font-size:.93rem}
 th,td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid #e3e5e8;
       vertical-align:top}
 th{font-weight:600;color:#555} tbody tr{cursor:pointer} tbody tr:hover{background:#f7f8fa}
 tr.open{background:#eef4ff}
 .tag{display:inline-block;border-radius:1rem;padding:0 .55rem;margin:0 .25rem .2rem 0;
      font-size:.82rem;background:#e8eaed;white-space:nowrap}
 .confirmed{background:#d6f1dd}.probable{background:#fdebc8}.emergency{background:#fbd5d5}
 .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
 #call{margin:1.2rem 0 3rem}
 .line{display:grid;grid-template-columns:4.2rem 1fr;gap:.2rem .8rem;padding:.45rem 0;
       border-bottom:1px solid #eef0f2}
 .when{color:#777;font-size:.82rem;font-variant-numeric:tabular-nums}
 .heard{font-weight:500}.said{color:#0b4a8f}
 .meta,.note{color:#777;font-size:.82rem}
 .back .heard,.back .said{color:#999;text-decoration:line-through}
 @media(max-width:40rem){.wide{display:none}}
</style>
<h1>Llamadas</h1>
<p>Lo que ha atendido el asistente: quién llamó, qué se dijo, qué hizo y qué costó el
modelo. Pulse una llamada para leerla entera.</p>
<p><input id="key" type="password" placeholder="Clave" autocomplete="current-password">
<button id="open">Abrir</button></p>
<div id="said"></div>
<div id="totals"></div>
<table hidden><thead><tr><th>Cuándo<th>Teléfono<th class="wide">Lengua<th>Quién
<th>Qué pasó<th class="num">Duración<th class="num wide">Turnos<th class="num">Modelo
</thead><tbody id="calls"></tbody></table>
<div id="call"></div>
<script>
const $ = id => document.getElementById(id);
// Everything a caller said is put on the page as text, never as markup.
const el = (tag, text, cls) => {
  const made = document.createElement(tag);
  if (text !== undefined && text !== null) made.textContent = text;
  if (cls) made.className = cls;
  return made;
};
const ask = path => fetch(path, {headers: {"Authorization": "Bearer " + $("key").value}});
const money = dollars => "$" + (dollars || 0).toFixed(4);
const length = seconds => Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
const when = iso => iso.slice(8, 10) + "/" + iso.slice(5, 7) + " " + iso.slice(11, 16);
const WHO = {none: "sin identificar", probable: "probable", confirmed: "identificado"};
const WHAT = {booked: "cita reservada", moved: "cita cambiada", cancelled: "cita anulada",
  message: "recado", emergency: "teléfono de urgencias dado", record: "ficha con errata",
  put_through: "pasada a una persona"};
const NOTE = {greeting: "saludo", silence: "silencio", hung_up: "el asistente cuelga",
  put_through: "se pasa la llamada", not_put_through: "nadie cogió el traspaso",
  trouble: "fallo al contestar"};
const note = what => what.startsWith("language:")
  ? "la plataforma pasa a escuchar en " + what.slice(9) : (NOTE[what] || what);

const show = async (id, row) => {
  const answer = await ask("/calls/" + encodeURIComponent(id));
  if (!answer.ok) return;
  const call = await answer.json(), box = $("call");
  document.querySelectorAll("tr.open").forEach(open => open.className = "");
  row.className = "open";
  box.replaceChildren(el("h2", "Llamada del " + when(call.started)));
  box.append(el("p", (call.caller || "número oculto") + " · " + call.id, "meta"));
  for (const line of call.lines) {
    const block = el("div", null, "line" + (line.taken_back ? " back" : ""));
    block.append(el("div", line.at.slice(11, 19), "when"));
    const body = el("div");
    if (line.heard) body.append(el("div", "— " + line.heard, "heard"));
    if (line.said) body.append(el("div", line.said, "said"));
    const meta = [];
    if (line.note) meta.push(note(line.note));
    if (line.taken_back) meta.push("no se oyó: la frase llegó otra vez, escrita distinta");
    for (const tool of line.tools) meta.push(tool.name + (tool.ok ? "" : " (rechazada)"));
    if (line.requests) {
      meta.push((line.first_words ?? line.seconds).toFixed(1) + " s hasta hablar");
      meta.push(line.tokens_in + " + " + line.tokens_out + " tokens");
      if (line.dollars !== null) meta.push(money(line.dollars));
    }
    if (meta.length) body.append(el("div", meta.join(" · "), "meta"));
    block.append(body);
    box.append(block);
  }
  box.scrollIntoView({behavior: "smooth"});
};

$("open").onclick = async () => {
  const answer = await ask("/calls");
  if (!answer.ok) {
    $("said").textContent = answer.status === 401 ? "La clave no es correcta."
                                                  : "No se ha podido abrir.";
    return;
  }
  $("said").textContent = "";
  const {totals, calls} = await answer.json();
  const each = totals.calls ? totals.dollars / totals.calls : 0;
  $("totals").replaceChildren(...[[totals.calls, "llamadas"], [totals.turns, "turnos"],
    [money(totals.dollars), "modelo, en total"], [money(each), "modelo, por llamada"]]
    .map(([value, label]) => { const box = el("div", label); box.prepend(el("b", value));
                                return box; }));
  $("calls").replaceChildren(...calls.map(call => {
    const row = el("tr");
    row.append(el("td", when(call.started)), el("td", call.caller || "oculto"),
               el("td", call.language || "", "wide"));
    const who = el("td");
    who.append(el("span", WHO[call.identity] || call.identity, "tag " + call.identity));
    if (call.client) who.append(el("div", call.client, "meta"));
    const what = el("td");
    for (const kind of call.happened) what.append(el("span", WHAT[kind] || kind, "tag " + kind));
    row.append(who, what, el("td", length(call.seconds), "num"),
               el("td", call.turns, "num wide"), el("td", money(call.dollars), "num"));
    row.onclick = () => show(call.id, row);
    return row;
  }));
  document.querySelector("table").hidden = false;
};
</script></html>
"""
