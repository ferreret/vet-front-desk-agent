"""Name pools for the synthetic clinic.

Every person, pet and place is a random combination drawn from these lists. Earlier entries
are drawn more often, which is what makes homonyms appear on their own. Any resemblance to a
real person is accidental: no real data source is involved.
"""

from .text import fold

# Catalan given name -> the Castilian form an old record (or a speech recogniser) would use.
CA_TO_ES_GIVEN = {
    "Joan": "Juan",
    "Antoni": "Antonio",
    "Miquel": "Miguel",
    "Jaume": "Jaime",
    "Pere": "Pedro",
    "Josep": "José",
    "Francesc": "Francisco",
    "Bartomeu": "Bartolomé",
    "Guillem": "Guillermo",
    "Llorenç": "Lorenzo",
    "Rafel": "Rafael",
    "Andreu": "Andrés",
    "Mateu": "Mateo",
    "Bernat": "Bernardo",
    "Pau": "Pablo",
    "Marc": "Marcos",
    "Jordi": "Jorge",
    "Xavier": "Javier",
    "Lluís": "Luis",
    "Carles": "Carlos",
    "Ferran": "Fernando",
    "Enric": "Enrique",
    "Vicenç": "Vicente",
    "Sebastià": "Sebastián",
    "Damià": "Damián",
    "Martí": "Martín",
    "Tomàs": "Tomás",
    "Maria": "María",
    "Margalida": "Margarita",
    "Francesca": "Francisca",
    "Antònia": "Antonia",
    "Joana": "Juana",
    "Caterina": "Catalina",
    "Aina": "Ana",
    "Mercè": "Mercedes",
    "Neus": "Nieves",
    "Esperança": "Esperanza",
    "Carme": "Carmen",
    "Dolors": "Dolores",
    "Lluïsa": "Luisa",
    "Júlia": "Julia",
    "Elisabet": "Isabel",
    "Apol·lònia": "Apolonia",
}

GIVEN = {
    "ca": {
        "M": [
            "Joan", "Antoni", "Miquel", "Jaume", "Pere", "Josep", "Francesc", "Bartomeu",
            "Guillem", "Llorenç", "Rafel", "Andreu", "Mateu", "Bernat", "Pau", "Marc", "Jordi",
            "Xavier", "Lluís", "Carles", "Ferran", "Enric", "Vicenç", "Sebastià", "Damià",
            "Martí", "Tomàs", "Biel", "Arnau", "Oriol", "Tomeu", "Gabriel",
        ],
        "F": [
            "Maria", "Margalida", "Francesca", "Antònia", "Joana", "Caterina", "Aina", "Mercè",
            "Neus", "Esperança", "Carme", "Dolors", "Lluïsa", "Júlia", "Elisabet", "Núria",
            "Montserrat", "Magdalena", "Rosa", "Marta", "Xisca", "Bel", "Apol·lònia",
        ],
    },
    "es": {
        "M": [
            "Antonio", "José", "Manuel", "Francisco", "Juan", "David", "Javier", "Daniel",
            "Carlos", "Miguel", "Jesús", "Alejandro", "Rafael", "Pedro", "Pablo", "Ángel",
            "Sergio", "Fernando", "Luis", "Jorge", "Alberto", "Álvaro", "Diego", "Adrián",
            "Raúl", "Iván", "Rubén", "Enrique", "Óscar", "Andrés", "Ramón", "Vicente",
            "Joaquín", "Víctor", "Eduardo",
        ],
        "F": [
            "María", "Carmen", "Ana", "Isabel", "Laura", "Cristina", "Marta", "Pilar", "Lucía",
            "Elena", "Rosa", "Raquel", "Sara", "Paula", "Beatriz", "Patricia", "Silvia", "Rocío",
            "Mónica", "Nuria", "Teresa", "Irene", "Alicia", "Sonia", "Eva", "Julia", "Marina",
            "Inés", "Lorena", "Yolanda", "Dolores", "Mercedes", "Josefa", "Francisca",
            "Antonia", "Catalina", "Margarita",
        ],
    },
}

SURNAMES = {
    "es": [
        "García", "Martínez", "López", "Sánchez", "Pérez", "Gómez", "Fernández", "González",
        "Rodríguez", "Ruiz", "Hernández", "Jiménez", "Díaz", "Moreno", "Muñoz", "Álvarez",
        "Romero", "Navarro", "Torres", "Domínguez", "Vázquez", "Ramos", "Gil", "Serrano",
        "Molina", "Blanco", "Suárez", "Castro", "Ortega", "Rubio", "Marín", "Sanz", "Núñez",
        "Medina", "Garrido", "Cortés", "Castillo", "Lozano", "Guerrero", "Cano", "Prieto",
        "Méndez", "Cruz", "Herrera", "Peña", "Flores", "Cabrera", "Campos", "Vega",
    ],
    "ca": [
        "Ferrer", "Vidal", "Serra", "Puig", "Soler", "Roca", "Pons", "Bosch", "Mas", "Riera",
        "Font", "Sastre", "Oliver", "Bauzà", "Llull", "Mir", "Amengual", "Vich", "Coll",
        "Ribas", "Sala", "Vila", "Costa", "Rosselló", "Mateu", "Company", "Moll", "Tous",
        "Bennàssar", "Cardona", "Colom", "Fiol", "Gelabert", "Llabrés", "Mayol", "Palmer",
        "Quetglas", "Ramis", "Salom", "Verger", "Alomar", "Bibiloni", "Crespí", "Estelrich",
        "Gomila", "Massanet", "Obrador", "Perelló", "Rigo", "Socias", "Truyols", "Vallespir",
        "Capó", "Ginard", "Munar", "Canals", "Pascual", "Planas", "Miralles", "Batlle",
        "Esteve", "Guasch", "Torrents",
    ],
}

PET_NAMES = [
    "Luna", "Coco", "Toby", "Rocky", "Nala", "Kira", "Max", "Lola", "Thor", "Simba", "Bimba",
    "Leo", "Mia", "Nina", "Bruno", "Zeus", "Maya", "Chispa", "Duna", "Rex", "Linda", "Trufa",
    "Canela", "Pelusa", "Bolita", "Michi", "Misi", "Tigre", "Copito", "Kiko", "Pipo", "Laika",
    "Dana", "Noa", "Bella", "Jack", "Golfo", "Negrito", "Blanquita", "Garfield", "Nemo",
    "Tambor", "Lucky", "Sombra", "Princesa", "Chiqui", "Yaki", "Bobby", "Daisy", "Oreo",
]

PET_NAMES_CA = [
    "Lluna", "Xispa", "Núvol", "Mel", "Bru", "Pruna", "Taca", "Petit", "Menut", "Fosca",
    "Neu", "Roc", "Trasto", "Tro", "Boira", "Estel", "Moixeta", "Xoco", "Llamp", "Nuca",
    "Pebre", "Sucre", "Garrofa", "Ametlla",
]

# species -> (weight, breeds, legacy spellings of the species; the first one is the canonical)
SPECIES = {
    "Perro": (
        55,
        [
            "Mestizo", "Labrador", "Pastor Alemán", "Yorkshire", "Bulldog Francés", "Chihuahua",
            "Podenco", "Ca de Bestiar", "Ca Rater", "Golden Retriever", "Border Collie",
            "Teckel", "Bichón Maltés", "Caniche", "Cocker", "Beagle", "Bóxer", "Galgo",
        ],
        ["Perro", "PERRO", "perro", "Canino", "Can"],
    ),
    "Gato": (
        33,
        ["Común Europeo", "Siamés", "Persa", "Maine Coon", "Británico", "Sphynx", "Bengalí"],
        ["Gato", "GATO", "gato", "Felino"],
    ),
    "Conejo": (4, ["Enano", "Belier", "Cabeza de León"], ["Conejo", "CONEJO"]),
    "Ave": (3, ["Periquito", "Canario", "Agapornis", "Ninfa", "Yaco"], ["Ave", "Pájaro"]),
    "Hurón": (2, ["Hurón"], ["Hurón", "HURON"]),
    "Cobaya": (2, ["Cobaya"], ["Cobaya", "Cobaia"]),
    "Tortuga": (1, ["De tierra", "De agua"], ["Tortuga"]),
}

COATS = [
    "Negro", "Blanco", "Marrón", "Atigrado", "Canela", "Gris", "Tricolor", "Blanco y negro",
    "Rubio", "Crema",
]

# Fictional towns and postcodes.
TOWNS = [
    ("Vallserena", "07990"),
    ("Port Blau", "07991"),
    ("Pinar del Mar", "07992"),
    ("Son Clar", "07993"),
    ("Cala Tramuntana", "07994"),
    ("Santa Aina del Camp", "07995"),
]

STREETS = {
    "es": [
        "Calle Mayor", "Calle del Sol", "Avenida de la Estación", "Calle de la Luna",
        "Plaza de España", "Calle del Mar", "Calle Nueva", "Camino Viejo",
    ],
    "ca": [
        "Carrer Major", "Carrer del Sol", "Carrer de sa Lluna", "Plaça de l'Església",
        "Carrer de la Mar", "Carrer Nou", "Camí Vell", "Avinguda de la Pau",
    ],
}

CLIENT_NOTES = [
    "Llamar por la tarde",
    "Paga en efectivo",
    "Avisar al móvil de la hija",
    "No llamar antes de las 10",
    "Prefiere que le hablen en catalán",
    "Pendiente de recoger informe",
]

PHONE_NOTES = ["hija", "trabajo", "marido", "mañanas", "madre", "fijo casa"]

_ES_BY_FOLDED_CA = {fold(ca): fold(es) for ca, es in CA_TO_ES_GIVEN.items()}


def given_key(given: str) -> str:
    """Comparison key for a given name; Catalan and Castilian forms of a name share a key."""
    folded = fold(given)
    return _ES_BY_FOLDED_CA.get(folded, folded)
