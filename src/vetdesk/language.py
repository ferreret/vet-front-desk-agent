"""Which language a caller is speaking, from the words of one line.

The model is told to follow the caller's language, and measured on a voice line it did
not: greeted in Catalan, it went on in Spanish until asked "no parles català?". So the
language is worked out here, from words that belong to one language and not the other, and
the model is told. Only the two languages the agent speaks are told apart; a line with no
telling word (a name, "sí", a number in figures) says nothing, and the call stays as it was.

Words that are not on the lists on purpose: "del" and "al", which belong to both languages
and come in the names of towns ("Pinar del Mar"), and "i" and "y", which are also letters:
a name spelled out, "V-I-D-A-L", turned a call in Spanish into Catalan.
"""

from __future__ import annotations

import re

_WORDS = {
    "ca": """bon bona dia tarda nit vull voldria agradaria demanar meu meva meus meves seu seva
        gos gossa gat és què amb però perquè gràcies adéu plau sisplau avui demà dilluns dimarts
        dimecres dijous divendres dissabte diumenge matí vespre setmana aquesta aquest això puc
        pot podria tinc té fer fa cama nom cognom cognoms parles parla català sóc soc truco
        trucar trucada veure estic quan ens us li hi ho els les dels als quatre cinc sis set
        vuit nou deu res més molt molta moltes bé acord per em dic diu diuen visc viu poble
        cita'm vinc venir vaig anem doncs també només cap ningú alguna algun sense fins""",
    "es": """buenos buenas días tardes noches quiero quería querría gustaría pedir mi mis su
        sus perro perra gato qué con pero porque gracias adiós favor hoy mañana lunes martes
        miércoles jueves viernes sábado domingo tarde semana esta este esto puedo puede podría
        tengo tiene hacer hace duele pata nombre apellido apellidos hablas habla español
        castellano soy llamo llamar llamada ver estoy cuándo cómo dónde nos le lo los las
        uno cuatro cinco seis siete ocho nueve diez cero nada más mucho mucha muchas bien
        vale acuerdo por para me digo dice vivo vive pueblo vengo voy vamos pues también
        solo ningún ninguna alguna algún sin hasta""",
}
_TELLING = {language: frozenset(words.split()) for language, words in _WORDS.items()}
_TELLING = {language: words - frozenset().union(*(other for name, other in _TELLING.items()
                                                 if name != language))
            for language, words in _TELLING.items()}
_WORD = re.compile(r"[^\W\d_]+")


def spoken_language(text: str) -> str | None:
    """'es' or 'ca' when the line's words tell, None when they do not."""
    words = _WORD.findall(text.lower())
    scores = {language: sum(word in telling for word in words)
              for language, telling in _TELLING.items()}
    best = max(scores, key=scores.get)
    others = [score for language, score in scores.items() if language != best]
    return best if scores[best] > max(others) else None
