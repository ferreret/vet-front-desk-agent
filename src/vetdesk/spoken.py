"""Days and times written out the way they are said, in each language the agent speaks.

A model asked to turn "16:30" into Catalan said "les cinc i mitja" to six callers out of
eighty-two: an hour late for their appointment. Asked on a voice line when the clinic
opens, it began "a las cinco y media menos... perdone, a las 16:30". A time the caller
will act on is not left to the model. It is handed over already in words, and the model
repeats them.
"""

from __future__ import annotations

from datetime import datetime, time

_ES = {
    "weekdays": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "months": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"),
    "hours": ("doce", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
              "nueve", "diez", "once"),
    "minutes": {0: "", 5: " y cinco", 10: " y diez", 15: " y cuarto", 20: " y veinte",
                25: " y veinticinco", 30: " y media", 35: " y treinta y cinco",
                40: " y cuarenta", 45: " y cuarenta y cinco", 50: " y cincuenta",
                55: " y cincuenta y cinco"},
}
_CA = {
    "weekdays": ("dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte",
                 "diumenge"),
    "months": ("gener", "febrer", "març", "abril", "maig", "juny", "juliol", "agost",
               "setembre", "octubre", "novembre", "desembre"),
    "hours": ("dotze", "una", "dues", "tres", "quatre", "cinc", "sis", "set", "vuit", "nou",
              "deu", "onze"),
    # Not the quarters system ("dos quarts de cinc"): the plain form is the usual one in the
    # Balearic Islands and cannot be misread by an hour.
    "minutes": {0: "", 5: " i cinc", 10: " i deu", 15: " i quart", 20: " i vint",
                25: " i vint-i-cinc", 30: " i mitja", 35: " i trenta-cinc", 40: " i quaranta",
                45: " i quaranta-cinc", 50: " i cinquanta", 55: " i cinquanta-cinc"},
}


_EN = {
    "weekdays": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "months": ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"),
    "hours": ("twelve", "one", "two", "three", "four", "five", "six", "seven", "eight",
              "nine", "ten", "eleven"),
    # The hour and then the minutes, as figures are read out: no "half past", which a
    # caller from another country may take for half an hour before.
    "minutes": {0: " o'clock", 5: " oh five", 10: " ten", 15: " fifteen", 20: " twenty",
                25: " twenty-five", 30: " thirty", 35: " thirty-five", 40: " forty",
                45: " forty-five", 50: " fifty", 55: " fifty-five"},
}


def _part_es(hour: int) -> str:
    if hour < 6:
        return "de la madrugada"
    if hour < 12:
        return "de la mañana"
    if hour == 12:
        return "del mediodía"
    return "de la tarde" if hour < 21 else "de la noche"


def _part_ca(hour: int) -> str:
    if hour < 6:
        return "de la matinada"
    if hour < 12:
        return "del matí"
    if hour < 15:
        return "del migdia"
    if hour < 20:
        return "de la tarda"
    return "del vespre" if hour < 22 else "de la nit"


def _minutes(words: dict, minute: int, joiner: str) -> str:
    return words["minutes"].get(minute, f" {joiner} {minute}".replace("  ", " "))


def say_es(moment: datetime) -> str:
    """'lunes 9 de noviembre a las cuatro y media de la tarde'"""
    hour = _ES["hours"][moment.hour % 12]
    article = "a la" if hour == "una" else "a las"
    return (f"{_ES['weekdays'][moment.weekday()]} {moment.day} de "
            f"{_ES['months'][moment.month - 1]} {article} {hour}"
            f"{_minutes(_ES, moment.minute, 'y')} {_part_es(moment.hour)}")


def say_ca(moment: datetime) -> str:
    """'dilluns 9 de novembre a les quatre i mitja de la tarda'"""
    hour = _CA["hours"][moment.hour % 12]
    article = "a la" if hour == "una" else "a les"
    month = _CA["months"][moment.month - 1]
    of = "d'" if month[0] in "aeiou" else "de "
    return (f"{_CA['weekdays'][moment.weekday()]} {moment.day} {of}{month} {article} {hour}"
            f"{_minutes(_CA, moment.minute, 'i')} {_part_ca(moment.hour)}")


def _part_en(hour: int) -> str:
    if hour < 12:
        return "in the morning"
    if hour < 18:
        return "in the afternoon"
    return "in the evening" if hour < 22 else "at night"


def say_en(moment: datetime) -> str:
    """'Monday 9 November at four thirty in the afternoon'"""
    day = f"{_EN['weekdays'][moment.weekday()]} {moment.day} {_EN['months'][moment.month - 1]}"
    if (moment.hour, moment.minute) == (12, 0):
        return f"{day} at twelve noon"
    return (f"{day} at {_EN['hours'][moment.hour % 12]}"
            f"{_minutes(_EN, moment.minute, '')} {_part_en(moment.hour)}")


SAY = {"es": say_es, "ca": say_ca, "en": say_en}


def say(moment: datetime, language: str) -> str:
    """A day and a time in words, in the language of the call."""
    return SAY[language](moment)


def clock_es(moment: time) -> str:
    """'las cuatro y media de la tarde', 'la una de la tarde'"""
    hour = _ES["hours"][moment.hour % 12]
    article = "la" if hour == "una" else "las"
    return f"{article} {hour}{_minutes(_ES, moment.minute, 'y')} {_part_es(moment.hour)}"
