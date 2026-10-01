import random

import pytest

from vetdesk.synth.asr_noise import corrupt


def test_no_noise_leaves_the_text_alone():
    assert corrupt("Margalida Ferrer Oliver", "none", random.Random(1)) == "Margalida Ferrer Oliver"


def test_noise_is_reproducible():
    said = "Llorenç Bauzà Quetglas"
    assert corrupt(said, "heavy", random.Random(7)) == corrupt(said, "heavy", random.Random(7))


@pytest.mark.parametrize("said", ["Joan Puig Vich", "Xisca Llull Amengual", "Pablo Muñoz González"])
def test_heavy_noise_changes_names(said):
    for seed in range(20):
        assert corrupt(said, "heavy", random.Random(seed)) != said


def test_light_noise_changes_a_single_word():
    said = "Margalida Ferrer Oliver"
    for seed in range(20):
        heard = corrupt(said, "light", random.Random(seed)).split()
        assert len(heard) == 3
        assert sum(a != b for a, b in zip(said.split(), heard, strict=True)) == 1


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError):
        corrupt("Luna", "extreme", random.Random(1))
