import pytest

from vetdesk.synth import GeneratorConfig, generate_world
from vetdesk.synth.scenarios import generate_scenarios


@pytest.fixture(scope="session")
def world():
    return generate_world(GeneratorConfig(seed=42))


@pytest.fixture(scope="session")
def scenarios(world):
    return generate_scenarios(world)
