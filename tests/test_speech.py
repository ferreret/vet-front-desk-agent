"""Simulated speech recognition: the caller says one thing, the agent hears another."""

from vetdesk.evals.speech import SpeechChannel, spelled_words
from vetdesk.scenario import Speech, Utterance


def _channel(*pairs, noise="light"):
    utterances = [Utterance(field=field, said=said, heard=heard) for field, said, heard in pairs]
    return SpeechChannel(Speech(noise=noise, utterances=utterances))


def test_without_noise_the_agent_hears_what_was_said():
    channel = _channel(("client_name", "Marta Pons Vidal", "Marta Pons Vidal"), noise="none")
    assert channel.hear("Soy Marta Pons Vidal, de Vallserena.") == \
        "Soy Marta Pons Vidal, de Vallserena."


def test_a_whole_name_is_heard_as_the_scenario_says():
    channel = _channel(("client_name", "Pablo Muñoz González", "Pavlo Muños"),
                       ("town", "Santa Aina del Camp", "Santa Aína del"))
    assert channel.hear("Me llamo Pablo Muñoz González.") == "Me llamo Pavlo Muños."
    assert channel.hear("me llamo pablo muñoz gonzález") == "me llamo Pavlo Muños"
    assert channel.hear("Vivo en Santa Aina del Camp") == "Vivo en Santa Aína del"


def test_part_of_a_name_is_garbled_the_same_way():
    channel = _channel(("client_name", "Pablo Muñoz González", "Pavlo Muños"),
                       ("pet_name", "Lluna", "Yuna"))
    assert channel.hear("Muñoz, con eñe") == "Muños, con eñe"
    assert channel.hear("Es para Lluna, la gata") == "Es para Yuna, la gata"
    # The surname the recogniser swallowed is heard when said on its own.
    assert channel.hear("González") == "González"


def test_words_are_matched_whole_and_as_written():
    channel = _channel(("client_name", "Maria Mas Sala", "Maria Ma Sala"))
    assert channel.hear("No tengo mas tiempo, soy Maria Mas") == "No tengo mas tiempo, soy Maria Ma"
    assert channel.hear("Masía Salas") == "Masía Salas"


def test_the_fuller_name_wins_over_its_parts():
    """One surname first, both when asked: each is heard as the scenario recorded it."""
    channel = _channel(("client_name", "Carme Llull", "Carme Yuy"),
                       ("client_name", "Carme Llull Munar", "Carme Llull Muna"))
    assert channel.hear("Carme Llull") == "Carme Yuy"
    assert channel.hear("Carme Llull Munar") == "Carme Llull Muna"
    assert channel.hear("Llull Munar") == "Yuy Muna"


def test_what_was_heard_is_not_garbled_again():
    channel = _channel(("client_name", "Pau Pons", "Pons Pau"))
    assert channel.hear("Pau Pons") == "Pons Pau"


def test_spelling_gets_through_untouched():
    channel = _channel(("client_name", "Xisca Ruiz Vidal", "Sisca Ruis"))
    spelled = "X-I-S-C-A R-U-I-Z V-I-D-A-L"
    assert channel.hear(f"Sí: {spelled}") == f"Sí: {spelled}"
    assert spelled_words(f"Sí: {spelled}.") == ["XISCA", "RUIZ", "VIDAL"]
    assert spelled_words("M-U-Ñ-O-Z, G-O-N-Z-Á-L-E-Z") == ["MUÑOZ", "GONZÁLEZ"]
    assert spelled_words("Es un cruce de pastor-alemán, tiene 3-4 años") == []


def test_every_scenario_is_heard_as_recorded(scenarios):
    for scenario in scenarios:
        channel = SpeechChannel(scenario.speech)
        for utterance in scenario.speech.utterances:
            assert channel.hear(utterance.said) == utterance.heard, scenario.id


def test_a_name_is_spelled_when_its_letters_are_there_in_order():
    """Word breaks and accents are not what spelling is about: a real recogniser returned
    "M-I-Q-U-E-L-R-O-S-S-E-L-L-O" for a first name and a surname spelled in one breath."""
    from vetdesk.identity.spelling import was_spelled

    in_one_breath = spelled_words("Se lo deletreo: M-I-Q-U-E-L-R-O-S-S-E-L-L-O.")
    assert in_one_breath == ["MIQUELROSSELLO"]
    assert was_spelled("Miquel Rosselló", in_one_breath)
    assert was_spelled("Rosselló", in_one_breath)
    assert not was_spelled("Miquel Rossellón", in_one_breath)  # a letter nobody said
    assert not was_spelled("Miquel Rosselló López", in_one_breath)  # a surname never spelled

    in_two_lines = spelled_words("M-I-Q-U-E-L") + spelled_words("R-O-S-S-E-L-L-Ó L-Ó-P-E-Z")
    assert was_spelled("Miquel Rosselló López", in_two_lines)
    assert not was_spelled("Miquel López", in_two_lines)  # letters skipped
    assert not was_spelled("Miquel", []) and not was_spelled("", in_two_lines)
