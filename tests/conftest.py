import pytest

from vetdesk.legacy import LegacySqliteSource
from vetdesk.synth import GeneratorConfig, generate_world
from vetdesk.synth.legacy_db import write_legacy_db
from vetdesk.synth.scenarios import generate_scenarios


@pytest.fixture(scope="session")
def world():
    return generate_world(GeneratorConfig(seed=42))


@pytest.fixture(scope="session")
def scenarios(world):
    return generate_scenarios(world)


@pytest.fixture(scope="session")
def clinic(world, tmp_path_factory):
    """The clinic as the adapter reads it from the legacy database."""
    path = tmp_path_factory.mktemp("legacy") / "clinic.db"
    write_legacy_db(world, path)
    return LegacySqliteSource(path).load()


@pytest.fixture(scope="session")
def client_ids(world):
    """Legacy code -> ground-truth client id. Only tests may know this mapping."""
    return {c.legacy_codigo: c.client_id for c in world.clients.values()}


@pytest.fixture(scope="session")
def truth(world):
    """Who is who, for the harness. The agent side never sees it."""
    from vetdesk.evals.truth import Truth
    from vetdesk.synth.legacy_db import export_truth

    return Truth(export_truth(world))
