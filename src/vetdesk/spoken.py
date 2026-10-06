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


# German, French, Italian and Russian tell the time of an appointment by the 24-hour clock,
# which is how it is said at a reception desk there and leaves nothing to mistake.
_DE = {
    "weekdays": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "months": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
               "September", "Oktober", "November", "Dezember"),
    "hours": ("null", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
              "zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn",
              "siebzehn", "achtzehn", "neunzehn", "zwanzig", "einundzwanzig",
              "zweiundzwanzig", "dreiundzwanzig"),
    "minutes": {0: "", 5: " fünf", 10: " zehn", 15: " fünfzehn", 20: " zwanzig",
                25: " fünfundzwanzig", 30: " dreißig", 35: " fünfunddreißig", 40: " vierzig",
                45: " fünfundvierzig", 50: " fünfzig", 55: " fünfundfünfzig"},
}
_FR = {
    "weekdays": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
    "months": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
               "septembre", "octobre", "novembre", "décembre"),
    "hours": ("zéro", "une", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
              "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize", "dix-sept",
              "dix-huit", "dix-neuf", "vingt", "vingt et une", "vingt-deux", "vingt-trois"),
    "minutes": {0: "", 5: " cinq", 10: " dix", 15: " quinze", 20: " vingt", 25: " vingt-cinq",
                30: " trente", 35: " trente-cinq", 40: " quarante", 45: " quarante-cinq",
                50: " cinquante", 55: " cinquante-cinq"},
}
_IT = {
    "weekdays": ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"),
    "months": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
               "settembre", "ottobre", "novembre", "dicembre"),
    "hours": ("zero", "una", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove",
              "dieci", "undici", "dodici", "tredici", "quattordici", "quindici", "sedici",
              "diciassette", "diciotto", "diciannove", "venti", "ventuno", "ventidue",
              "ventitré"),
    "minutes": {0: "", 5: " e cinque", 10: " e dieci", 15: " e quindici", 20: " e venti",
                25: " e venticinque", 30: " e trenta", 35: " e trentacinque", 40: " e quaranta",
                45: " e quarantacinque", 50: " e cinquanta", 55: " e cinquantacinque"},
}
_RU = {
    "weekdays": ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота",
                 "воскресенье"),
    "months": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
               "сентября", "октября", "ноября", "декабря"),
    "hours": ("ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь",
              "девять", "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
              "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
              "двадцать", "двадцать один", "двадцать два", "двадцать три"),
    "minutes": {0: "", 5: " ноль пять", 10: " десять", 15: " пятнадцать", 20: " двадцать",
                25: " двадцать пять", 30: " тридцать", 35: " тридцать пять", 40: " сорок",
                45: " сорок пять", 50: " пятьдесят", 55: " пятьдесят пять"},
}


def _day(words: dict, moment: datetime) -> tuple[str, str]:
    return words["weekdays"][moment.weekday()], words["months"][moment.month - 1]


def say_de(moment: datetime) -> str:
    """'Montag, 9. November, um sechzehn Uhr dreißig'"""
    weekday, month = _day(_DE, moment)
    return (f"{weekday}, {moment.day}. {month}, um {_DE['hours'][moment.hour]} Uhr"
            f"{_minutes(_DE, moment.minute, '')}")


def say_fr(moment: datetime) -> str:
    """'lundi 9 novembre à seize heures trente'"""
    weekday, month = _day(_FR, moment)
    day = "1er" if moment.day == 1 else str(moment.day)
    hours = "heure" if moment.hour in (0, 1) else "heures"
    return (f"{weekday} {day} {month} à {_FR['hours'][moment.hour]} {hours}"
            f"{_minutes(_FR, moment.minute, '')}")


def say_it(moment: datetime) -> str:
    """'lunedì 9 novembre alle sedici e trenta'"""
    weekday, month = _day(_IT, moment)
    day = "1º" if moment.day == 1 else str(moment.day)
    at = "all'una" if moment.hour == 1 else f"alle {_IT['hours'][moment.hour]}"
    return f"{weekday} {day} {month} {at}{_minutes(_IT, moment.minute, 'e')}"


def say_ru(moment: datetime) -> str:
    """'понедельник, 9 ноября, в шестнадцать тридцать'"""
    weekday, month = _day(_RU, moment)
    hour = moment.hour
    if moment.minute:
        time_said = f"{_RU['hours'][hour]}{_minutes(_RU, moment.minute, '')}"
    else:  # один час, два часа, пять часов, двадцать один час
        last = hour % 10
        unit = "час" if last == 1 and hour != 11 else \
            "часа" if last in (2, 3, 4) and hour not in (12, 13, 14) else "часов"
        time_said = f"{_RU['hours'][hour]} {unit}"
    return f"{weekday}, {moment.day} {month}, в {time_said}"


SAY = {"es": say_es, "ca": say_ca, "en": say_en, "de": say_de, "fr": say_fr, "it": say_it,
       "ru": say_ru}


def say(moment: datetime, language: str) -> str:
    """A day and a time in words, in the language of the call."""
    return SAY[language](moment)


def clock_es(moment: time) -> str:
    """'las cuatro y media de la tarde', 'la una de la tarde'"""
    hour = _ES["hours"][moment.hour % 12]
    article = "la" if hour == "una" else "las"
    return f"{article} {hour}{_minutes(_ES, moment.minute, 'y')} {_part_es(moment.hour)}"
