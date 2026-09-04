from __future__ import annotations

import logging
import random
import re
import string
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyWordSearch

logger = logging.getLogger(__name__)
SIZE = 12
THEMES = [
    ("Space", ["GALAXY", "PLANET", "COMET", "ORBIT", "NEBULA", "ROCKET", "SATURN", "LUNAR", "METEOR", "COSMOS"]),
    ("India", ["GANGES", "LOTUS", "MONSOON", "HIMALAYA", "SAFFRON", "DELHI", "MUMBAI", "BENGAL", "DECCAN", "DIWALI"]),
    ("Nature", ["FOREST", "RIVER", "OCEAN", "CANYON", "GLACIER", "VOLCANO", "MEADOW", "THUNDER", "BREEZE", "SUNSET"]),
    ("Science", ["ATOM", "CARBON", "ENERGY", "NEURON", "PHOTON", "PLASMA", "MAGNET", "GRAVITY", "OXYGEN", "QUARTZ"]),
    ("Newspaper", ["EDITOR", "COLUMN", "BYLINE", "PRESS", "DAILY", "REPORT", "PUZZLE", "HEADLINE", "JOURNAL", "ARTICLE"]),
    ("Wildlife", ["FALCON", "PANDA", "TIGER", "DOLPHIN", "COBRA", "PEACOCK", "TURTLE", "LEOPARD", "RABBIT", "WHALE"]),
    ("Geography", ["ISLAND", "DESERT", "VALLEY", "PLATEAU", "DELTA", "LAGOON", "TUNDRA", "SAVANNA", "ARCTIC", "EQUATOR"]),

    # --- India ---------------------------------------------------------
    ("Indian Rivers", ["YAMUNA", "KRISHNA", "GODAVARI", "NARMADA", "KAVERI", "BEAS", "CHENAB", "TAPTI", "MAHANADI", "SUTLEJ"]),
    ("Indian Cities", ["CHENNAI", "KOLKATA", "JAIPUR", "LUCKNOW", "INDORE", "KOCHI", "PATNA", "SURAT", "NAGPUR", "BHOPAL"]),
    ("Indian States", ["KERALA", "PUNJAB", "ODISHA", "GUJARAT", "ASSAM", "MANIPUR", "TRIPURA", "SIKKIM", "GOA", "HARYANA"]),
    ("Indian Festivals", ["HOLI", "PONGAL", "ONAM", "BAISAKHI", "NAVRATRI", "HORNBILL", "LOHRI", "BIHU", "DUSSEHRA", "UGADI"]),
    ("Indian Cuisine", ["BIRYANI", "SAMOSA", "DOSA", "PANEER", "CHUTNEY", "KORMA", "PULAO", "RAITA", "TIKKA", "HALWA"]),
    ("Indian Spices", ["TURMERIC", "CARDAMOM", "CUMIN", "CLOVE", "PEPPER", "FENNEL", "GINGER", "MUSTARD", "NUTMEG", "CINNAMON"]),
    ("Indian Sweets", ["LADDU", "JALEBI", "BARFI", "RASGULLA", "PEDA", "KHEER", "MODAK", "HALWA", "MYSOREPAK", "SANDESH"]),
    ("Street Food", ["CHAAT", "PANIPURI", "VADAPAV", "BHELPURI", "KATHIROLL", "MOMO", "PAKORA", "IDLI", "UTTAPAM", "FALOODA"]),
    ("Indian Monuments", ["TAJMAHAL", "QUTUBMINAR", "REDFORT", "HAWAMAHAL", "AJANTA", "ELLORA", "KHAJURAHO", "AMBER", "GOLCONDA", "SANCHI"]),
    ("Indian Music", ["SITAR", "TABLA", "VEENA", "SARANGI", "SHEHNAI", "TANPURA", "MRIDANGAM", "SANTOOR", "FLUTE", "RAGA"]),
    ("Indian Dance", ["SATTRIYA", "KATHAK", "ODISSI", "KUCHIPUDI", "MOHINIYATTAM", "MANIPURI", "BHANGRA", "GARBA", "LAVANI", "KATHAKALI"]),
    ("Indian Wildlife", ["GHARIAL", "NILGAI", "SAMBAR", "LANGUR", "MACAQUE", "PANGOLIN", "HORNBILL", "BARASINGHA", "CHINKARA", "DHOLE"]),
    ("Indian Birds", ["KOEL", "MYNA", "BULBUL", "PARAKEET", "KINGFISHER", "DRONGO", "SUNBIRD", "EGRET", "LAPWING", "BABBLER"]),
    ("Indian Trees", ["BANYAN", "PEEPAL", "NEEM", "TEAK", "SANDAL", "MANGO", "TAMARIND", "GULMOHAR", "ASHOKA", "MAHUA"]),
    ("Indian Languages", ["HINDI", "TAMIL", "TELUGU", "MARATHI", "KANNADA", "BENGALI", "ODIA", "PUNJABI", "URDU", "MALAYALAM"]),
    ("Indian Textiles", ["KHADI", "SILK", "COTTON", "BROCADE", "MUSLIN", "IKAT", "CHIKAN", "BANDHANI", "PASHMINA", "JAMDANI"]),
    ("Indian Railways", ["PLATFORM", "SIGNAL", "COACH", "ENGINE", "JUNCTION", "SLEEPER", "PANTRY", "TICKET", "SIDING", "EXPRESS"]),
    ("Himalayas", ["EVEREST", "LHOTSE", "NANDADEVI", "ANNAPURNA", "SHERPA", "GLACIER", "AVALANCHE", "SUMMIT", "RIDGE", "CREVASSE"]),
    ("Indian Ocean", ["MONSOON", "LAKSHADWEEP", "ANDAMAN", "NICOBAR", "CORAL", "MANGROVE", "CYCLONE", "CURRENT", "TRENCH", "ATOLL"]),
    ("Ayurveda", ["TULSI", "ASHWAGANDHA", "BRAHMI", "AMLA", "HALDI", "SHATAVARI", "TRIPHALA", "GILOY", "DOSHA", "HERBAL"]),
    ("Yoga", ["ASANA", "PRANAYAMA", "MUDRA", "CHAKRA", "MANTRA", "SHAVASANA", "VINYASA", "BALANCE", "BREATH", "POSTURE"]),
    ("Cricket", ["WICKET", "BOWLER", "INNINGS", "BOUNDARY", "STUMPS", "CREASE", "SPINNER", "FIELDER", "CENTURY", "UMPIRE"]),
    ("Indian Independence", ["SWARAJ", "CHARKHA", "DANDI", "TRICOLOUR", "ASHOKA", "REPUBLIC", "FREEDOM", "MARCH", "PLEDGE", "UNITY"]),
    ("Temples", ["GOPURAM", "SHIKHARA", "MANDAPA", "GARBHA", "PILLAR", "CARVING", "GRANITE", "SHRINE", "BELL", "LAMP"]),
    ("Indian Markets", ["BAZAAR", "HAGGLE", "STALL", "VENDOR", "BASKET", "SPICE", "FABRIC", "BANGLE", "POTTERY", "GARLAND"]),

    # --- World geography ------------------------------------------------
    ("World Rivers", ["AMAZON", "DANUBE", "VOLGA", "MEKONG", "YANGTZE", "CONGO", "RHINE", "THAMES", "ZAMBEZI", "COLORADO"]),
    ("Mountains", ["ALPS", "ANDES", "ROCKIES", "URALS", "ATLAS", "ZAGROS", "PYRENEES", "CASCADE", "SIERRA", "CAUCASUS"]),
    ("Deserts", ["SAHARA", "GOBI", "KALAHARI", "MOJAVE", "ATACAMA", "SONORAN", "NAMIB", "PATAGONIA", "ARABIAN", "TAKLAMAKAN"]),
    ("Islands", ["MADAGASCAR", "SUMATRA", "BORNEO", "CRETE", "SICILY", "CUBA", "TASMANIA", "ICELAND", "FIJI", "MALDIVES"]),
    ("Oceans and Seas", ["PACIFIC", "ATLANTIC", "BALTIC", "CASPIAN", "ADRIATIC", "AEGEAN", "CORAL", "BERING", "WEDDELL", "SARGASSO"]),
    ("Capitals", ["OTTAWA", "LISBON", "VIENNA", "HELSINKI", "NAIROBI", "HAVANA", "MANILA", "ANKARA", "OSLO", "PRAGUE"]),
    ("Volcanoes", ["VESUVIUS", "ETNA", "KRAKATOA", "FUJI", "COTOPAXI", "MAUNALOA", "STROMBOLI", "PINATUBO", "MAGMA", "CRATER"]),
    ("Rainforest", ["CANOPY", "LIANA", "ORCHID", "TOUCAN", "JAGUAR", "TAPIR", "HUMID", "FERN", "SLOTH", "UNDERSTORY"]),
    ("Polar Regions", ["ICEBERG", "PENGUIN", "WALRUS", "TUNDRA", "AURORA", "PERMAFROST", "FLOE", "BLIZZARD", "SLEDGE", "NARWHAL"]),
    ("Caves", ["STALAGMITE", "CHAMBER", "LIMESTONE", "CAVERN", "TUNNEL", "SINKHOLE", "GROTTO", "ECHO", "MINERAL", "DARKNESS"]),

    # --- Science and technology -----------------------------------------
    ("Chemistry", ["MOLECULE", "ISOTOPE", "CATALYST", "SOLVENT", "ACID", "ALKALI", "CRYSTAL", "POLYMER", "REAGENT", "VALENCE"]),
    ("Physics", ["VELOCITY", "INERTIA", "FRICTION", "MOMENTUM", "VOLTAGE", "CURRENT", "PRISM", "LENS", "WAVE", "TORQUE"]),
    ("Biology", ["CELL", "TISSUE", "ENZYME", "PROTEIN", "MITOSIS", "SPECIES", "HABITAT", "ORGANISM", "NUCLEUS", "MEMBRANE"]),
    ("Astronomy", ["QUASAR", "PULSAR", "ECLIPSE", "ASTEROID", "CRATER", "TELESCOPE", "SUPERNOVA", "GALAXY", "AURORA", "ZENITH"]),
    ("Genetics", ["GENOME", "CHROMOSOME", "MUTATION", "HELIX", "ALLELE", "TRAIT", "HEREDITY", "CLONE", "SEQUENCE", "MARKER"]),
    ("Computers", ["KEYBOARD", "MONITOR", "PROCESSOR", "MEMORY", "STORAGE", "NETWORK", "SOFTWARE", "PIXEL", "CURSOR", "BINARY"]),
    ("Internet", ["BROWSER", "SERVER", "ROUTER", "DOMAIN", "PACKET", "UPLOAD", "STREAM", "COOKIE", "SEARCH", "BANDWIDTH"]),
    ("Robotics", ["SENSOR", "ACTUATOR", "CIRCUIT", "GRIPPER", "SERVO", "CHASSIS", "FEEDBACK", "AUTONOMY", "MOTOR", "PROGRAM"]),
    ("Mathematics", ["ALGEBRA", "GEOMETRY", "FRACTION", "INTEGER", "MATRIX", "VECTOR", "THEOREM", "TANGENT", "PRIME", "RATIO"]),
    ("Medicine", ["VACCINE", "SURGEON", "DIAGNOSIS", "ANTIBODY", "CLINIC", "REMEDY", "DOSAGE", "SUTURE", "PULSE", "THERAPY"]),
    ("Anatomy", ["SKELETON", "TENDON", "ARTERY", "CORNEA", "LIVER", "SPINE", "MUSCLE", "KIDNEY", "MARROW", "CARTILAGE"]),
    ("Minerals", ["GRANITE", "BASALT", "GYPSUM", "FELDSPAR", "MICA", "OBSIDIAN", "MARBLE", "SLATE", "PYRITE", "TOPAZ"]),
    ("Fossils", ["AMBER", "IMPRINT", "SEDIMENT", "TRILOBITE", "AMMONITE", "RELIC", "EXCAVATE", "STRATA", "PETRIFY", "SPECIMEN"]),
    ("Dinosaurs", ["RAPTOR", "STEGOSAURUS", "TRICERATOPS", "PTEROSAUR", "FOSSIL", "JURASSIC", "CRETACEOUS", "HERBIVORE", "PREDATOR", "EXTINCT"]),
    ("Weather", ["CYCLONE", "DRIZZLE", "HUMIDITY", "FORECAST", "PRESSURE", "HAILSTONE", "OVERCAST", "SQUALL", "MONSOON", "FROST"]),
    ("Energy", ["SOLAR", "TURBINE", "REACTOR", "BATTERY", "BIOMASS", "THERMAL", "GRID", "VOLTAGE", "FUSION", "DYNAMO"]),

    # --- Arts and culture -----------------------------------------------
    ("Painting", ["CANVAS", "PALETTE", "PIGMENT", "BRUSH", "EASEL", "PORTRAIT", "MURAL", "FRESCO", "SHADING", "VARNISH"]),
    ("Sculpture", ["CHISEL", "MARBLE", "BRONZE", "RELIEF", "CASTING", "MODEL", "STONE", "CARVE", "PLINTH", "TORSO"]),
    ("Architecture", ["ARCH", "DOME", "COLUMN", "FACADE", "VAULT", "ATRIUM", "BALCONY", "TERRACE", "SPIRE", "CORNICE"]),
    ("Photography", ["SHUTTER", "APERTURE", "LENS", "TRIPOD", "EXPOSURE", "FOCUS", "PORTRAIT", "NEGATIVE", "STUDIO", "FILTER"]),
    ("Cinema", ["DIRECTOR", "SCRIPT", "CAMERA", "EDITING", "SCENE", "TRAILER", "PREMIERE", "COSTUME", "SOUNDTRACK", "STUDIO"]),
    ("Theatre", ["STAGE", "CURTAIN", "REHEARSE", "MONOLOGUE", "BACKSTAGE", "PROPS", "LIGHTING", "AUDIENCE", "SCRIPT", "APPLAUSE"]),
    ("Literature", ["NOVEL", "CHAPTER", "NARRATOR", "PLOT", "PROSE", "SATIRE", "MEMOIR", "FABLE", "EPILOGUE", "IMAGERY"]),
    ("Poetry", ["SONNET", "STANZA", "RHYME", "METER", "COUPLET", "VERSE", "BALLAD", "HAIKU", "ELEGY", "REFRAIN"]),
    ("Instruments", ["VIOLIN", "TRUMPET", "PIANO", "CELLO", "HARP", "OBOE", "BANJO", "DRUM", "CLARINET", "ACCORDION"]),
    ("Museums", ["GALLERY", "EXHIBIT", "CURATOR", "ARTEFACT", "ARCHIVE", "DISPLAY", "CATALOGUE", "RESTORE", "COLLECTION", "PLAQUE"]),
    ("Mythology", ["ORACLE", "TITAN", "PHOENIX", "CENTAUR", "OLYMPUS", "TRIDENT", "CHARIOT", "LEGEND", "MORTAL", "PROPHECY"]),
    ("Folklore", ["TALE", "RIDDLE", "PROVERB", "TRICKSTER", "CHARM", "LANTERN", "WANDER", "VILLAGE", "STORYTELLER", "CUSTOM"]),

    # --- Food and drink --------------------------------------------------
    ("Fruits", ["MANGO", "PAPAYA", "GUAVA", "LYCHEE", "APRICOT", "CHERRY", "BANANA", "MELON", "PLUM", "POMEGRANATE"]),
    ("Vegetables", ["SPINACH", "CARROT", "PUMPKIN", "CABBAGE", "BRINJAL", "RADISH", "TURNIP", "OKRA", "BEETROOT", "LETTUCE"]),
    ("Grains", ["WHEAT", "BARLEY", "MILLET", "QUINOA", "SORGHUM", "OATS", "RYE", "MAIZE", "BASMATI", "BUCKWHEAT"]),
    ("Herbs", ["BASIL", "THYME", "OREGANO", "PARSLEY", "ROSEMARY", "MINT", "SAGE", "CHIVE", "DILL", "CORIANDER"]),
    ("Baking", ["FLOUR", "YEAST", "KNEAD", "OVEN", "PASTRY", "BATTER", "GLAZE", "CRUST", "WHISK", "SPONGE"]),
    ("Beverages", ["COFFEE", "MASALA", "LASSI", "SHERBET", "NECTAR", "INFUSION", "BREW", "CIDER", "SMOOTHIE", "COCOA"]),
    ("Desserts", ["PUDDING", "SORBET", "MOUSSE", "CUSTARD", "TRIFLE", "BROWNIE", "PRALINE", "TOFFEE", "GELATO", "MERINGUE"]),
    ("Seafood", ["PRAWN", "LOBSTER", "MACKEREL", "SARDINE", "OYSTER", "SQUID", "CRAB", "MUSSEL", "POMFRET", "SALMON"]),
    ("Breakfast", ["PORRIDGE", "OMELETTE", "PANCAKE", "TOAST", "CEREAL", "YOGURT", "HONEY", "JUICE", "MUFFIN", "GRANOLA"]),

    # --- Nature ----------------------------------------------------------
    ("Flowers", ["JASMINE", "MARIGOLD", "ORCHID", "TULIP", "DAHLIA", "HIBISCUS", "LAVENDER", "PRIMROSE", "DAISY", "CAMELLIA"]),
    ("Trees", ["MAPLE", "CEDAR", "WILLOW", "BIRCH", "POPLAR", "SPRUCE", "CYPRESS", "WALNUT", "CHESTNUT", "JUNIPER"]),
    ("Insects", ["BEETLE", "CRICKET", "DRAGONFLY", "MANTIS", "APHID", "TERMITE", "HORNET", "WEEVIL", "FIREFLY", "LOCUST"]),
    ("Butterflies", ["MONARCH", "SWALLOW", "ADMIRAL", "PAINTED", "CHRYSALIS", "NECTAR", "ANTENNA", "MIGRATE", "PUPA", "MEADOW"]),
    ("Reptiles", ["IGUANA", "GECKO", "PYTHON", "VIPER", "TORTOISE", "MONITOR", "SKINK", "CHAMELEON", "ALLIGATOR", "CROCODILE"]),
    ("Mammals", ["OTTER", "BADGER", "BISON", "CAMEL", "GIRAFFE", "LEMUR", "MOOSE", "PORCUPINE", "WOMBAT", "MEERKAT"]),
    ("Fish", ["TROUT", "CARP", "TUNA", "ANCHOVY", "HERRING", "CATFISH", "GROUPER", "MARLIN", "PIRANHA", "STINGRAY"]),
    ("Seashore", ["PEBBLE", "DRIFTWOOD", "SEAWEED", "BARNACLE", "TIDEPOOL", "DUNE", "SHELL", "BREAKER", "STARFISH", "LIGHTHOUSE"]),
    ("Garden", ["TROWEL", "COMPOST", "SEEDLING", "PRUNE", "HEDGE", "TRELLIS", "MULCH", "SPROUT", "GREENHOUSE", "WATERING"]),
    ("Mushrooms", ["MOREL", "TRUFFLE", "SHIITAKE", "OYSTER", "BUTTON", "SPORE", "MYCELIUM", "FUNGUS", "CANOPY", "DECAY"]),

    # --- Transport and places -------------------------------------------
    ("Aircraft", ["FUSELAGE", "PROPELLER", "COCKPIT", "RUDDER", "HANGAR", "GLIDER", "ALTITUDE", "RUNWAY", "TURBINE", "AILERON"]),
    ("Ships", ["ANCHOR", "MAST", "HARBOUR", "RUDDER", "GALLEY", "SCHOONER", "FERRY", "CARGO", "PORTHOLE", "STARBOARD"]),
    ("Trains", ["LOCOMOTIVE", "CARRIAGE", "TRACK", "TUNNEL", "STATION", "SIGNAL", "FREIGHT", "WHISTLE", "SHUNT", "TIMETABLE"]),
    ("Cars", ["ENGINE", "CLUTCH", "GEARBOX", "CHASSIS", "BUMPER", "IGNITION", "RADIATOR", "EXHAUST", "STEERING", "BRAKE"]),
    ("Bicycles", ["PEDAL", "SPOKE", "HANDLEBAR", "SADDLE", "CHAIN", "GEAR", "HELMET", "TYRE", "FRAME", "BRAKE"]),
    ("Bridges", ["SUSPENSION", "GIRDER", "CABLE", "PYLON", "ARCH", "SPAN", "TRUSS", "VIADUCT", "PIER", "DECK"]),
    ("Airport", ["TERMINAL", "BOARDING", "LUGGAGE", "CUSTOMS", "GATE", "DEPARTURE", "ARRIVAL", "CHECKIN", "TARMAC", "TRANSIT"]),
    ("Hotel", ["LOBBY", "SUITE", "CONCIERGE", "RESERVE", "BALCONY", "LAUNDRY", "PORTER", "BUFFET", "CHECKOUT", "CORRIDOR"]),
    ("Library", ["SHELF", "CATALOGUE", "BORROW", "VOLUME", "REFERENCE", "SILENCE", "PERIODICAL", "ARCHIVE", "BINDING", "READING"]),
    ("Hospital", ["WARD", "SURGERY", "NURSE", "TRIAGE", "GURNEY", "PHARMACY", "MONITOR", "SCALPEL", "RECOVERY", "STERILE"]),
    ("School", ["BLACKBOARD", "SATCHEL", "LESSON", "RECESS", "UNIFORM", "ASSEMBLY", "HOMEWORK", "TEACHER", "CHALK", "REGISTER"]),
    ("Office", ["DESK", "STAPLER", "MEETING", "AGENDA", "FOLDER", "PRINTER", "MEMO", "CUBICLE", "ROSTER", "DEADLINE"]),
    ("Bank", ["LEDGER", "DEPOSIT", "INTEREST", "VAULT", "CHEQUE", "BALANCE", "CASHIER", "ACCOUNT", "TRANSFER", "LOAN"]),

    # --- Sport ------------------------------------------------------------
    ("Football", ["STRIKER", "GOALIE", "MIDFIELD", "PENALTY", "OFFSIDE", "CORNER", "DRIBBLE", "TACKLE", "WHISTLE", "STADIUM"]),
    ("Tennis", ["RACQUET", "VOLLEY", "BASELINE", "DEUCE", "SERVE", "RALLY", "TIEBREAK", "COURT", "SMASH", "BACKHAND"]),
    ("Athletics", ["SPRINT", "HURDLE", "JAVELIN", "DISCUS", "RELAY", "MARATHON", "VAULT", "SHOTPUT", "TRACK", "STARTER"]),
    ("Swimming", ["FREESTYLE", "BUTTERFLY", "BACKSTROKE", "LENGTH", "GOGGLES", "DIVING", "LANE", "STROKE", "POOL", "FLIPTURN"]),
    ("Olympics", ["MEDAL", "TORCH", "PODIUM", "ANTHEM", "RELAY", "VILLAGE", "OPENING", "MASCOT", "RECORD", "CEREMONY"]),
    ("Chess", ["BISHOP", "KNIGHT", "CASTLE", "PAWN", "GAMBIT", "CHECKMATE", "STALEMATE", "OPENING", "ENDGAME", "BLUNDER"]),
    ("Hockey", ["DRIBBLE", "PENALTY", "GOALIE", "STICK", "CORNER", "TURF", "TACKLE", "FLICK", "MIDFIELD", "WHISTLE"]),
    ("Badminton", ["SHUTTLE", "RACQUET", "SMASH", "SERVICE", "RALLY", "NET", "COURT", "DROPSHOT", "LOB", "UMPIRE"]),
    ("Cycling", ["PELOTON", "SPRINT", "CLIMB", "JERSEY", "TIMETRIAL", "SADDLE", "GEAR", "DESCENT", "CIRCUIT", "DOMESTIQUE"]),
    ("Motorsport", ["CIRCUIT", "PITSTOP", "CHICANE", "FORMULA", "OVERTAKE", "TELEMETRY", "APEX", "PODIUM", "QUALIFY", "CHEQUERED"]),

    # --- Everyday ---------------------------------------------------------
    ("Kitchen", ["LADLE", "SKILLET", "GRATER", "COLANDER", "SPATULA", "KETTLE", "CUTLERY", "SIMMER", "PANTRY", "MORTAR"]),
    ("Tools", ["HAMMER", "WRENCH", "PLIERS", "CHISEL", "DRILL", "SANDER", "CLAMP", "MALLET", "SPANNER", "SCREWDRIVER"]),
    ("Furniture", ["ARMCHAIR", "DRESSER", "OTTOMAN", "BOOKCASE", "WARDROBE", "CABINET", "STOOL", "BENCH", "MATTRESS", "SIDEBOARD"]),
    ("Clothing", ["JACKET", "TROUSERS", "SWEATER", "BLOUSE", "SCARF", "MITTEN", "WAISTCOAT", "PYJAMAS", "OVERALL", "CARDIGAN"]),
    ("Jewellery", ["NECKLACE", "PENDANT", "BANGLE", "BROOCH", "ANKLET", "EMERALD", "SAPPHIRE", "PEARL", "FILIGREE", "LOCKET"]),
    ("Stationery", ["NOTEBOOK", "ERASER", "RULER", "ENVELOPE", "CLIPBOARD", "MARKER", "SHARPENER", "BINDER", "POSTCARD", "INKPOT"]),
    ("Camping", ["TENT", "LANTERN", "CAMPFIRE", "RUCKSACK", "COMPASS", "SLEEPING", "SKEWER", "TRAIL", "CANTEEN", "KINDLING"]),
    ("Weather at Home", ["UMBRELLA", "RAINCOAT", "GALOSHES", "SHUTTER", "AWNING", "HEATER", "BLANKET", "FIREPLACE", "DRAUGHT", "THERMOSTAT"]),
    ("Colours", ["CRIMSON", "AZURE", "MAROON", "OLIVE", "INDIGO", "AMBER", "TURQUOISE", "LILAC", "SCARLET", "EMERALD"]),
    ("Shapes", ["TRIANGLE", "HEXAGON", "PENTAGON", "OCTAGON", "SPHERE", "CYLINDER", "PYRAMID", "ELLIPSE", "RHOMBUS", "TRAPEZIUM"]),

    # --- Professions -------------------------------------------------------
    ("Farming", ["HARVEST", "PLOUGH", "IRRIGATE", "ORCHARD", "SILO", "TRACTOR", "FURROW", "GRANARY", "SOWING", "THRESHER"]),
    ("Journalism", ["INTERVIEW", "DEADLINE", "SOURCE", "BULLETIN", "BROADCAST", "EDITORIAL", "STRINGER", "NEWSROOM", "DISPATCH", "MASTHEAD"]),
    ("Engineering", ["BLUEPRINT", "GIRDER", "SURVEY", "TOLERANCE", "CALIBRATE", "PROTOTYPE", "WELDING", "BEARING", "CONDUIT", "SCAFFOLD"]),
    ("Cooking Crafts", ["CHOPPING", "MARINATE", "GARNISH", "SEASON", "BRAISE", "POACHING", "REDUCE", "FILLET", "PLATING", "TASTING"]),
    ("Fishing", ["TRAWLER", "HARPOON", "NETTING", "TACKLE", "BAIT", "REEL", "ANGLER", "CATCH", "HARBOUR", "LOBSTER"]),
    ("Pottery", ["KILN", "GLAZE", "CLAY", "WHEEL", "TERRACOTTA", "MOULD", "FIRING", "CERAMIC", "SLIP", "BURNISH"]),
    ("Carpentry", ["TIMBER", "PLANE", "DOVETAIL", "VARNISH", "JOINERY", "SAWDUST", "MITRE", "LATHE", "VENEER", "MORTISE"]),
    ("Weaving", ["LOOM", "SHUTTLE", "WARP", "WEFT", "SPINDLE", "YARN", "TAPESTRY", "THREAD", "DYEING", "PATTERN"]),

    # --- Abstract / seasonal -----------------------------------------------
    ("Seasons", ["SPRING", "SUMMER", "AUTUMN", "WINTER", "HARVEST", "BLOSSOM", "EQUINOX", "SOLSTICE", "FOLIAGE", "THAW"]),
    ("Time", ["CALENDAR", "DECADE", "CENTURY", "MOMENT", "INTERVAL", "SUNDIAL", "MIDNIGHT", "DAWN", "DUSK", "ERA"]),
    ("Light", ["BEAM", "GLIMMER", "RADIANCE", "SHADOW", "REFLECT", "REFRACT", "LANTERN", "TWILIGHT", "GLARE", "PRISM"]),
    ("Sound", ["ECHO", "MURMUR", "RESONANCE", "WHISPER", "CHIME", "RHYTHM", "SILENCE", "TIMBRE", "VOLUME", "VIBRATION"]),
    ("Travel", ["ITINERARY", "PASSPORT", "SUITCASE", "JOURNEY", "VOYAGE", "EXPLORE", "SOUVENIR", "COMPASS", "LODGING", "DEPARTURE"]),
    ("Games", ["MARBLES", "DOMINO", "PUZZLE", "CHARADES", "HOPSCOTCH", "CAROM", "LUDO", "RIDDLE", "TOKEN", "SHUFFLE"]),
    ("Books", ["PREFACE", "GLOSSARY", "INDEX", "MARGIN", "HARDBACK", "PAPERBACK", "SPINE", "CHAPTER", "AUTHOR", "PUBLISH"]),
    ("Weather Signs", ["RAINBOW", "MIRAGE", "HALO", "DEWDROP", "MIST", "GUST", "DOWNPOUR", "CLOUDBURST", "SUNSHINE", "SHOWER"]),
    ("Emotions", ["DELIGHT", "WONDER", "CURIOSITY", "PATIENCE", "COURAGE", "SERENITY", "GRATITUDE", "EMPATHY", "HOPEFUL", "RELIEF"]),
    ("Money", ["CURRENCY", "BUDGET", "SAVINGS", "INVOICE", "PROFIT", "MARKET", "TRADING", "CAPITAL", "RECEIPT", "EXCHANGE"]),
]
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1), (0, -1), (-1, 0), (-1, -1), (-1, 1))


def theme_and_words_for_date(puzzle_date: date) -> tuple[str, list[str]]:
    """Deterministically pick a theme + word list from the curated THEMES
    bank for a given date — no AI involved, this was already
    dependency-free before the APIVerve swap."""
    seed = int(puzzle_date.strftime("%Y%m%d"))
    theme, source_words = THEMES[seed % len(THEMES)]
    return theme, list(source_words)


def place_words(puzzle_date: date, words: list[str]) -> tuple[list[str], list[str]]:
    """Pack `words` into the grid, returning (grid rows, sorted placed words).
    Unchanged from the original algorithm — only the source of `words` has
    changed (LLM-generated theme vs. the fixed THEMES rotation)."""
    seed = int(puzzle_date.strftime("%Y%m%d"))
    rng = random.Random(seed)
    words = list(words)
    rng.shuffle(words)
    grid: list[list[str | None]] = [[None for _ in range(SIZE)] for _ in range(SIZE)]

    # Longest-first makes the packing reliable while the shuffled equal-length
    # ordering and random direction/start choices keep each date visually new.
    for word in sorted(words, key=len, reverse=True):
        options = []
        for dr, dc in DIRECTIONS:
            for row in range(SIZE):
                for col in range(SIZE):
                    end_row, end_col = row + (len(word) - 1) * dr, col + (len(word) - 1) * dc
                    if not (0 <= end_row < SIZE and 0 <= end_col < SIZE):
                        continue
                    if all(grid[row + i * dr][col + i * dc] in (None, letter) for i, letter in enumerate(word)):
                        options.append((row, col, dr, dc))
        if not options:
            raise RuntimeError(f"Unable to place word: {word}")
        row, col, dr, dc = rng.choice(options)
        for i, letter in enumerate(word):
            grid[row + i * dr][col + i * dc] = letter

    for row in range(SIZE):
        for col in range(SIZE):
            if grid[row][col] is None:
                grid[row][col] = rng.choice(string.ascii_uppercase)
    return ["".join(row) for row in grid], sorted(words)


async def get_or_create_word_search(session: AsyncSession, puzzle_date: date) -> DailyWordSearch:
    result = await session.execute(select(DailyWordSearch).where(DailyWordSearch.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    lock_key = 75000000 + int(puzzle_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyWordSearch).where(DailyWordSearch.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    theme, source_words = theme_and_words_for_date(puzzle_date)
    # Fully local. APIVerve only ever supplied the grid layout here — the words
    # were always curated in THEMES — and place_words packs them deterministically,
    # so the call bought nothing but a credit a day.
    grid, words = place_words(puzzle_date, source_words)
    source = "algorithmic"
    puzzle = DailyWordSearch(puzzle_date=puzzle_date, theme=theme, grid=grid, words=words, source=source)
    session.add(puzzle)
    await session.commit()
    await session.refresh(puzzle)
    return puzzle
