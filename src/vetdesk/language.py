"""Which language a caller is speaking, from the words of one line.

The languages told apart here are the ones the agent speaks: adding one is adding its
words, how a day and a time are said in it (`spoken`), and the agent's own stock phrases.

The model is told to follow the caller's language, and measured on a voice line it did
not: greeted in Catalan, it went on in Spanish until asked "no parles català?". So the
language is worked out here, from words that belong to one language and to no other, and
the model is told. A line with no telling word (a name, "sí", a number in figures) says
nothing, and the call stays as it was.

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
    # No "hi", "us", "can", "on", "pet", "he", "has", "no", "me": they are words of Catalan
    # or Spanish too ("Can Pons" is a house, "on" is where).
    "en": """hello good morning afternoon evening would like want need book make appointment
        my dog cat rabbit the is it please thank thanks you your yes what when where how
        could have his her she they we not but there here this that with for and of to at
        today tomorrow monday tuesday wednesday thursday friday saturday sunday week speak
        english name phone number one two three four five six seven eight nine am are was
        does did will sick hurt limping vaccine checkup check emergency open closed price
        much""",
    # None of these three lists holds a word of another list: a word in two lists tells
    # neither language, and would stop telling the one it was already in.
    "de": """hallo guten morgen tag abend ich möchte brauche einen termin für mein meine meinen
        hund katze danke bitte ja nein ist und mit heute montag dienstag mittwoch donnerstag
        freitag samstag sonntag woche wann wie wo nummer telefon sprechen deutsch eins zwei
        drei vier fünf sechs sieben acht neun null nicht aber das der die den sie wir haben
        hat kann können uhr notfall vormittag nachmittag nächste nächsten geht gut auf
        wiederhören tschüss vielen dank impfung untersuchung krank hinkt geöffnet ihr ihren
        ihre namen welcher welche tage tagen möchten sagen""",
    "fr": """bonjour bonsoir je voudrais veux besoin prendre rendez vous pour mon chien chat
        merci oui est et avec plaît aujourd demain lundi mardi mercredi jeudi vendredi samedi
        dimanche matin soir après midi quand comment où numéro téléphone parlez français deux
        trois cinq sept huit neuf zéro ai elle ne pas très heure semaine urgence chez au
        revoir ça accord prochaine vaccin malade boite ouvert votre vos prénom animaux jour
        jours heures mes cette une quel quelle quels pouvez puis""",
    "it": """buongiorno buonasera salve vorrei voglio prenotare appuntamento mio cane gatto
        grazie sì oggi domani lunedì martedì mercoledì giovedì venerdì sabato domenica mattina
        pomeriggio sera quando dove nome numero telefono italiano due tre quattro cinque sei
        sette otto nove dieci è sono non ma gli che anche molto bene settimana urgenza posso
        può vuole arrivederci prossima vaccino malato zoppica aperto accordo suo sua suoi
        cognome favore chiamo giorno giorni ore alle della questo animali prego buon quale
        unghie""",
}
# Russian is told by its alphabet: a line with a Cyrillic letter in it is Russian.
_CYRILLIC = re.compile("[а-яё]")
_TELLING = {language: frozenset(words.split()) for language, words in _WORDS.items()}
_TELLING = {language: words - frozenset().union(*(other for name, other in _TELLING.items()
                                                 if name != language))
            for language, words in _TELLING.items()}
_WORD = re.compile(r"[^\W\d_]+")
# The languages this module can tell apart, which are the ones the agent speaks.
SPOKEN = (*_TELLING, "ru")


def spoken_language(text: str, least: int = 1) -> str | None:
    """The language a line's words tell, None when they do not.

    `least` is how many telling words it takes. One is enough for a greeting. It is not
    enough to change the language of a call that has found its own: among seven languages a
    single word is too often somebody else's ("le unghie" has a Spanish word in it, and
    "vaccination annuelle" an English one).
    """
    if _CYRILLIC.search(text.lower()):
        return "ru"
    words = _WORD.findall(text.lower())
    scores = {language: sum(word in telling for word in words)
              for language, telling in _TELLING.items()}
    best = max(scores, key=scores.get)
    others = [score for language, score in scores.items() if language != best]
    return best if scores[best] >= least and scores[best] > max(others) else None
