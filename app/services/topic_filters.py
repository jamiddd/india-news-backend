"""
Content-based keyword gates layered on top of Source.category for tabs where
"which RSS section this came from" isn't a reliable enough signal for what's
actually in the article.

Two different bugs prompted this:

1. Crypto/Science/Education/Health are seeded from broad publisher section
   feeds (e.g. Indian Express's combined "technology/science" section, HT's
   general "lifestyle/health" section) that carry plenty of stories with
   nothing to do with the tab they're filed under — a gadget review isn't
   science, a diet fad piece isn't exam/policy news. For these four, source
   category alone is too coarse, so membership also requires the title or
   snippet to actually mention something on-topic.

2. Northeast/regional_* tabs occasionally pick up an off-topic wire story
   that happened to run on a regional outlet (e.g. a general-newspaper feed
   carrying a syndicated piece with nothing to do with that region). Unlike
   (1), the *source* should remain the sole signal for whether an article
   belongs to a region at all — this keyword gate is a narrow safety net
   that only excludes articles which don't even mention the region, it does
   not add a competing "detect region from anywhere" mechanism. In
   particular, everything here is whole-word matched (Postgres `\\y`
   boundaries), so "Arunachaleswar" (a Tamil Nadu temple town) never matches
   the "Arunachal" keyword the way a plain substring check would.
"""
import re
from typing import Dict, List

CRYPTO_KEYWORDS = [
    "crypto", "cryptocurrency", "cryptocurrencies", "bitcoin", "btc", "ethereum",
    "eth", "blockchain", "altcoin", "altcoins", "defi", "nft", "nfts", "web3",
    "stablecoin", "stablecoins", "binance", "coinbase", "wazirx", "coindcx",
    "dogecoin", "litecoin", "solana", "ripple", "xrp", "satoshi", "cbdc",
    "digital rupee", "crypto exchange", "crypto wallet", "token sale",
]

SCIENCE_KEYWORDS = [
    "scientist", "scientists", "research", "researchers", "discovery",
    "discovered", "nasa", "isro", "spacex", "space station", "telescope",
    "astronomy", "astronomer", "physics", "quantum", "genome", "dna",
    "species", "fossil", "fossils", "archaeology", "rocket launch",
    "satellite launch", "black hole", "exoplanet", "particle", "evolution",
    "laboratory", "breakthrough", "climate study", "asteroid", "mars rover",
    "lunar mission", "solar eclipse",
]

EDUCATION_KEYWORDS = [
    "exam", "exams", "board exam", "board exams", "cbse", "icse", "neet",
    "jee", "ugc", "ncert", "aicte", "gate exam", "upsc", "university",
    "universities", "admission", "admissions", "scholarship", "scholarships",
    "syllabus", "curriculum", "school reopens", "education policy", "nep",
    "result declared", "results declared", "entrance exam", "semester",
    "college", "colleges", "students", "student",
]

HEALTH_KEYWORDS = [
    "health", "disease", "hospital", "doctor", "doctors", "wellness",
    "fitness", "mental health", "women's health", "men's health",
    "sexual health", "pregnancy", "nutrition", "diet", "vaccine",
    "vaccination", "obesity", "cancer", "diabetes", "biology", "virus",
    "outbreak", "body positivity", "therapy", "surgery", "medicine",
    "contraception", "menstrual", "menstruation", "fertility", "hormone",
    "hormones", "who guidelines",
]

NORTHEAST_KEYWORDS = [
    "assam", "arunachal", "manipur", "meghalaya", "mizoram", "nagaland",
    "sikkim", "tripura", "guwahati", "shillong", "imphal", "agartala",
    "itanagar", "aizawl", "kohima", "gangtok", "northeast india",
    "north east india", "north-east india", "bodoland", "brahmaputra",
    "dispur", "dimapur", "silchar", "tezpur", "jorhat",
]

REGIONAL_SOUTH_KEYWORDS = [
    "tamil nadu", "kerala", "karnataka", "andhra pradesh", "telangana",
    "chennai", "bengaluru", "bangalore", "hyderabad", "kochi", "coimbatore",
    "madurai", "thiruvananthapuram", "mysuru", "mysore", "vijayawada",
    "visakhapatnam", "puducherry", "tirupati", "mangaluru",
]

REGIONAL_WEST_KEYWORDS = [
    "maharashtra", "gujarat", "goa", "mumbai", "pune", "ahmedabad", "surat",
    "vadodara", "nagpur", "nashik", "panaji", "thane", "rajkot",
]

REGIONAL_EAST_KEYWORDS = [
    "west bengal", "odisha", "bihar", "jharkhand", "kolkata", "bhubaneswar",
    "patna", "ranchi", "siliguri", "cuttack", "durgapur", "howrah", "gaya",
]

CONTENT_GATED_CATEGORIES: Dict[str, List[str]] = {
    "crypto": CRYPTO_KEYWORDS,
    "science": SCIENCE_KEYWORDS,
    "education": EDUCATION_KEYWORDS,
    "health": HEALTH_KEYWORDS,
    "northeast": NORTHEAST_KEYWORDS,
    "regional_south": REGIONAL_SOUTH_KEYWORDS,
    "regional_west": REGIONAL_WEST_KEYWORDS,
    "regional_east": REGIONAL_EAST_KEYWORDS,
}


def keyword_regex(keywords: List[str]) -> str:
    """Build a Postgres ~* pattern matching any keyword on whole-word/phrase
    boundaries (\\y), so e.g. "Arunachaleswar" never matches "Arunachal"."""
    escaped = [re.escape(k) for k in keywords]
    return r"\y(" + "|".join(escaped) + r")\y"
