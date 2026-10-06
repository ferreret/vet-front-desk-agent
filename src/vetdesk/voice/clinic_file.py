"""The clinic's information as a file that can be replaced while the server runs.

Opening hours, prices and the emergency number live in one validated file. Built into the
server's image, changing a price meant a deployment and somebody who can make one. Here the
file sits on the server's disk: it is read when the server starts, and replaced through
the server itself, which checks the new text exactly as the bundled one is checked and
keeps the old one if the new one is wrong. A reception that mistypes a phone number is
told so; it does not take the phone line down.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..kb import KnowledgeBase, bundled_kb_text, parse_kb, why_not

log = logging.getLogger("vetdesk.clinic")


class ClinicFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(bundled_kb_text(), encoding="utf-8")
            log.info("the clinic's information was written to %s for the first time", path)
        try:
            self.kb = parse_kb(self.text())
        except Exception as error:  # a file somebody broke by hand must not stop the phone
            log.warning("the clinic's information in %s is not valid (%s): the one that "
                        "comes with the project is used", path, why_not(error))
            self.kb = parse_kb(bundled_kb_text())

    def text(self) -> str:
        return self._path.read_text(encoding="utf-8")

    def replace(self, text: str) -> KnowledgeBase:
        """Check `text` and, if it is a valid knowledge base, make it the clinic's.

        Raises whatever `parse_kb` raises, and then nothing has changed. The text it
        replaces is kept beside it, to go back to.
        """
        kb = parse_kb(text)
        previous = self._path.with_name(self._path.name + ".previous")
        previous.write_text(self.text(), encoding="utf-8")
        fresh = self._path.with_name(self._path.name + ".new")
        fresh.write_text(text, encoding="utf-8")
        os.replace(fresh, self._path)  # in one step: never half a file
        self.kb = kb
        return kb


PAGE = """<!doctype html>
<html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Información de la clínica</title>
<style>
 body{font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}
 textarea{width:100%;height:65vh;font:14px/1.45 ui-monospace,monospace;padding:.75rem;
          box-sizing:border-box}
 input,button{font:inherit;padding:.4rem .7rem} #said{margin:.8rem 0;white-space:pre-wrap}
 .ok{color:#0a6b2d}.bad{color:#b00020}
</style>
<h1>Información de la clínica</h1>
<p>Lo que el asistente puede decir por teléfono: horarios, servicios, precios, urgencias.
Al guardar se comprueba; si algo está mal no se cambia nada y se dice qué es. Vale para las
llamadas que entren a partir de ese momento.</p>
<p><input id="key" type="password" placeholder="Clave" autocomplete="current-password">
<button id="open">Abrir</button> <button id="save" disabled>Guardar</button></p>
<div id="said"></div>
<textarea id="text" spellcheck="false" disabled></textarea>
<script>
const $ = id => document.getElementById(id);
const say = (text, good) => {
  $("said").textContent = text; $("said").className = good ? "ok" : "bad";
};
const ask = (method, body) => fetch("/clinic", {method, body,
  headers: {"Authorization": "Bearer " + $("key").value}});
$("open").onclick = async () => {
  const answer = await ask("GET");
  if (!answer.ok) {
    return say(answer.status === 401 ? "La clave no es correcta." : "No se ha podido abrir.");
  }
  $("text").value = await answer.text();
  $("text").disabled = $("save").disabled = false;
  say("Abierto. Cambie lo que haga falta y pulse Guardar.", true);
};
$("save").onclick = async () => {
  const answer = await ask("PUT", $("text").value), told = await answer.json();
  say(answer.ok ? "Guardado. Ya vale para las próximas llamadas."
                : "No se ha guardado. " + (told.error || ""), answer.ok);
};
</script></html>
"""
