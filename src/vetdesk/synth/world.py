"""The clean world: the clinic as it really is, before the legacy system mangles it.

Everything the evaluation later treats as ground truth lives here. `defects.py` then decides
how each record is stored in the legacy database and labels every degradation it applies.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from . import names
from .names import given_key
from .text import fold

# Fixed "today" so the same seed always gives the same clinic.
REFERENCE_DATE = date(2026, 10, 1)


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 42
    n_clients: int = 400
    catalan_share: float = 0.45
    two_client_household_share: float = 0.15
    landline_share: float = 0.6
    # Planted traps, so every scenario category has material to work with.
    homonym_pairs: int = 8  # same full name, different households
    household_homonym_pairs: int = 5  # parent and child: same given name and first surname
    near_homonym_pairs: int = 6  # same given name and first surname, different households
    # Defect rates.
    no_pets_share: float = 0.10
    deceased_share: float = 0.05
    stale_phone_share: float = 0.12
    missing_phone_share: float = 0.04
    castilianized_share: float = 0.35
    link_drift_share: float = 0.08
    nani_stale_share: float = 0.06
    orphan_animals: int = 10

    @property
    def planted_clients(self) -> int:
        pairs = self.homonym_pairs + self.household_homonym_pairs + self.near_homonym_pairs
        return 2 * pairs

    def __post_init__(self) -> None:
        minimum = self.planted_clients + 60
        if self.n_clients < minimum:
            raise ValueError(f"n_clients must be at least {minimum} to cover every scenario")


@dataclass
class PhoneOnFile:
    number: str  # E.164
    column: str  # Telefono | Telefono2 | Movil
    status: str  # current | stale
    raw: str = ""  # as typed into the legacy field


@dataclass
class Household:
    household_id: str
    language: str
    town: str
    postcode: str
    address: str
    landline: str | None


@dataclass
class Client:
    client_id: str
    given: str
    surname1: str
    surname2: str
    sex: str
    language: str
    household_id: str
    mobile: str | None
    planted: tuple[str, int] | None = None
    pet_ids: list[str] = field(default_factory=list)
    email: str | None = None
    # How the legacy database stores this client (filled in by defects.py).
    legacy_codigo: int = 0
    legacy_name: str = ""
    surname2_on_file: bool = True
    phones_on_file: list[PhoneOnFile] = field(default_factory=list)
    nani_on_file: int = 0
    signup: date = REFERENCE_DATE
    notes: str = ""
    defects: list[str] = field(default_factory=list)


@dataclass
class Pet:
    pet_id: str
    name: str
    species: str
    breed: str
    sex: str
    born: date
    coat: str
    chip: str | None
    deceased: bool
    owner_id: str | None
    # How the legacy database stores this animal (filled in by defects.py).
    legacy_codigo: int = 0
    legacy_owner_name: str = ""
    legacy_species: str = ""
    # Clients the owner-name link can point to once spelling is cleaned up. More than one
    # means the legacy data alone cannot tell whose animal this is.
    linked_client_ids: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)


@dataclass
class World:
    config: GeneratorConfig
    households: dict[str, Household] = field(default_factory=dict)
    clients: dict[str, Client] = field(default_factory=dict)
    pets: dict[str, Pet] = field(default_factory=dict)
    planted: dict[str, list[list[str]]] = field(default_factory=dict)
    # Numbers still on file that the client no longer uses: who answers them now.
    stale_holders: dict[str, str] = field(default_factory=dict)  # number -> stranger | none
    used_numbers: set[str] = field(default_factory=set)

    def members(self, household_id: str) -> list[Client]:
        return [c for c in self.clients.values() if c.household_id == household_id]

    def current_phones(self, client: Client) -> list[str]:
        """Numbers this person really calls from, most personal first."""
        landline = self.households[client.household_id].landline
        phones = [n for n in (client.mobile, landline) if n]
        if not phones:
            phones = [m.mobile for m in self.members(client.household_id) if m.mobile][:1]
        return phones

    def phone_index(self) -> dict[str, list[str]]:
        """number -> clients whose record lists it."""
        index: dict[str, list[str]] = {}
        for client in self.clients.values():
            for phone in client.phones_on_file:
                ids = index.setdefault(phone.number, [])
                if client.client_id not in ids:
                    ids.append(client.client_id)
        return index

    def new_number(self, rng: random.Random, kind: str) -> str:
        prefix = "+346" if kind == "mobile" else "+34971"
        digits = 12 - len(prefix)
        while True:
            number = prefix + "".join(rng.choice("0123456789") for _ in range(digits))
            if number not in self.used_numbers:
                self.used_numbers.add(number)
                return number


def file_key(client: Client) -> tuple[str, str, str | None]:
    """What the legacy record can say about a name once its spelling is cleaned up."""
    surname2 = fold(client.surname2) if client.surname2_on_file else None
    return given_key(client.given), fold(client.surname1), surname2


@dataclass
class _Spec:
    given: str
    surname1: str
    surname2: str
    sex: str
    language: str
    group: tuple[str, int] | None = None


def _weighted(rng: random.Random, pool: list[str]) -> str:
    weights = [1 / (i + 1) ** 0.6 for i in range(len(pool))]
    return rng.choices(pool, weights)[0]


def _surname(rng: random.Random, language: str, avoid: tuple[str, ...] = ()) -> str:
    other = "es" if language == "ca" else "ca"
    while True:
        pool = names.SURNAMES[language if rng.random() < 0.7 else other]
        surname = _weighted(rng, pool)
        if surname not in avoid:
            return surname


def _person(rng: random.Random, language: str) -> _Spec:
    other = "es" if language == "ca" else "ca"
    sex = rng.choice("FM")
    given = _weighted(rng, names.GIVEN[language if rng.random() < 0.8 else other][sex])
    surname1 = _surname(rng, language)
    surname2 = _surname(rng, language, avoid=(surname1,))
    return _Spec(given, surname1, surname2, sex, language)


def _household_specs(cfg: GeneratorConfig, rng: random.Random) -> list[list[_Spec]]:
    def language() -> str:
        return "ca" if rng.random() < cfg.catalan_share else "es"

    taken: set[tuple[str, str]] = set()

    def fresh(group: tuple[str, int]) -> _Spec:
        while True:
            person = _person(rng, language())
            key = (given_key(person.given), fold(person.surname1))
            if key not in taken:
                taken.add(key)
                person.group = group
                return person

    def other_surname2(person: _Spec) -> str:
        return _surname(rng, person.language, avoid=(person.surname1, person.surname2))

    specs: list[list[_Spec]] = []
    for i in range(cfg.homonym_pairs):
        first = fresh(("homonym", i))
        specs += [[first], [replace(first)]]
    for i in range(cfg.household_homonym_pairs):
        parent = fresh(("household_homonym", i))
        specs.append([parent, replace(parent, surname2=other_surname2(parent))])
    for i in range(cfg.near_homonym_pairs):
        first = fresh(("near_homonym", i))
        specs += [[first], [replace(first, surname2=other_surname2(first))]]

    count = sum(len(members) for members in specs)
    while count < cfg.n_clients:
        lang = language()
        members = [_person(rng, lang)]
        if count + 2 <= cfg.n_clients and rng.random() < cfg.two_client_household_share:
            second = _person(rng, lang)
            if rng.random() < 0.4:  # an adult child or sibling registered separately
                second.surname1 = members[0].surname1
            members.append(second)
        specs.append(members)
        count += len(members)
    return specs


def _add_people(world: World, rng: random.Random) -> None:
    cfg = world.config
    specs = _household_specs(cfg, rng)
    rng.shuffle(specs)
    groups: dict[tuple[str, int], list[str]] = {}
    serial = 0
    for index, members in enumerate(specs, start=1):
        language = members[0].language
        needs_landline = any(m.group and m.group[0] == "household_homonym" for m in members)
        town, postcode = rng.choice(names.TOWNS)
        street = rng.choice(names.STREETS[language])
        has_landline = needs_landline or rng.random() < cfg.landline_share
        household = Household(
            household_id=f"H-{index:04d}",
            language=language,
            town=town,
            postcode=postcode,
            address=f"{street}, {rng.randint(1, 120)}",
            landline=world.new_number(rng, "landline") if has_landline else None,
        )
        world.households[household.household_id] = household
        for spec in members:
            serial += 1
            has_mobile = household.landline is None or rng.random() < 0.92
            client = Client(
                client_id=f"C-{serial:04d}",
                given=spec.given,
                surname1=spec.surname1,
                surname2=spec.surname2,
                sex=spec.sex,
                language=language,
                household_id=household.household_id,
                mobile=world.new_number(rng, "mobile") if has_mobile else None,
                planted=spec.group,
            )
            if rng.random() < 0.4:
                local = f"{fold(spec.given)}.{fold(spec.surname1)}".replace(" ", "")
                client.email = f"{local}{rng.randint(1, 99)}@example.com"
            world.clients[client.client_id] = client
            if spec.group:
                groups.setdefault(spec.group, []).append(client.client_id)
    for (kind, _), ids in sorted(groups.items()):
        world.planted.setdefault(kind, []).append(ids)


def _add_pets(world: World, rng: random.Random) -> None:
    cfg = world.config
    species_names = list(names.SPECIES)
    species_weights = [names.SPECIES[s][0] for s in species_names]
    group_pet_names: dict[tuple[str, int], set[str]] = {}
    serial = 0
    for client in world.clients.values():
        count = rng.choices([0, 1, 2, 3, 4], [cfg.no_pets_share, 0.55, 0.25, 0.08, 0.02])[0]
        if client.planted:
            count = max(count, 1)
        # Planted look-alikes never share a pet name, so the pet can tell them apart.
        avoid = group_pet_names.setdefault(client.planted, set()) if client.planted else set()
        own: set[str] = set()
        for position in range(count):
            use_catalan = client.language == "ca" and rng.random() < 0.35
            pool = names.PET_NAMES_CA if use_catalan else names.PET_NAMES
            while True:
                name = _weighted(rng, pool)
                if fold(name) not in own and fold(name) not in avoid:
                    break
            own.add(fold(name))
            species = rng.choices(species_names, species_weights)[0]
            age_days = rng.randint(120, 16 * 365)
            old = age_days > 10 * 365
            deceased = rng.random() < cfg.deceased_share * (2.5 if old else 0.5)
            if client.planted and position == 0:
                deceased = False
            chipped = species in ("Perro", "Gato") and rng.random() < 0.8
            serial += 1
            pet = Pet(
                pet_id=f"A-{serial:04d}",
                name=name,
                species=species,
                breed=rng.choice(names.SPECIES[species][1]),
                sex=rng.choice("MH"),
                born=REFERENCE_DATE - timedelta(days=age_days),
                coat=rng.choice(names.COATS),
                chip="".join(rng.choice("0123456789") for _ in range(15)) if chipped else None,
                deceased=deceased,
                owner_id=client.client_id,
            )
            world.pets[pet.pet_id] = pet
            client.pet_ids.append(pet.pet_id)
        avoid |= own


def build_clean_world(cfg: GeneratorConfig) -> World:
    rng = random.Random(f"{cfg.seed}/world")
    world = World(config=cfg)
    _add_people(world, rng)
    _add_pets(world, rng)
    return world
