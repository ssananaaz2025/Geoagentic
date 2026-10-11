"""
Built-in Bengaluru gazetteer.

Why this exists: the public Nominatim geocoder rate-limits / blocks many
networks, which used to make "choose source / destination" fail with a
502. These well-known places are always available, instantly and offline,
and are merged ahead of any live geocoder results (see live.py).
"""
from difflib import SequenceMatcher
from typing import List

PLACES = [
    # name, area, lat, lng, kind
    ("Vidhana Soudha", "Ambedkar Veedhi", 12.9796, 77.5907, "landmark"),
    ("Cubbon Park", "Kasturba Road", 12.9763, 77.5929, "landmark"),
    ("MG Road Metro", "MG Road", 12.9756, 77.6066, "transit"),
    ("Majestic (KSR Bengaluru City Jn)", "Majestic", 12.9783, 77.5726, "transit"),
    ("Kempegowda Bus Station", "Majestic", 12.9767, 77.5724, "transit"),
    ("Lalbagh Botanical Garden", "Lalbagh", 12.9507, 77.5848, "landmark"),
    ("Bowring & Lady Curzon Hospital", "Shivajinagar", 12.9866, 77.6016, "hospital"),
    ("Victoria Hospital", "Fort", 12.9634, 77.5744, "hospital"),
    ("NIMHANS", "Hosur Road", 12.9431, 77.5963, "hospital"),
    ("St. John's Medical College Hospital", "Koramangala", 12.9298, 77.6197, "hospital"),
    ("Manipal Hospital Old Airport Road", "Domlur", 12.9586, 77.6486, "hospital"),
    ("Narayana Health City", "Bommasandra", 12.8116, 77.6902, "hospital"),
    ("Fortis Hospital Bannerghatta Road", "Bannerghatta Road", 12.8955, 77.5983, "hospital"),
    ("Apollo Hospital Jayanagar", "Jayanagar", 12.9189, 77.5995, "hospital"),
    ("Kidwai Memorial Institute of Oncology", "Hosur Road", 12.9389, 77.5957, "hospital"),
    ("Indiranagar 100 Feet Road", "Indiranagar", 12.9784, 77.6408, "area"),
    ("Koramangala 5th Block", "Koramangala", 12.9352, 77.6245, "area"),
    ("HSR Layout", "HSR Layout", 12.9116, 77.6474, "area"),
    ("Jayanagar 4th Block", "Jayanagar", 12.9250, 77.5938, "area"),
    ("Malleshwaram", "Malleshwaram", 13.0035, 77.5646, "area"),
    ("Rajajinagar", "Rajajinagar", 12.9914, 77.5521, "area"),
    ("Whitefield", "Whitefield", 12.9698, 77.7480, "area"),
    ("Electronic City", "Electronic City", 12.8452, 77.6602, "area"),
    ("Hebbal Flyover", "Hebbal", 13.0358, 77.5970, "landmark"),
    ("Yeshwanthpur Railway Station", "Yeshwanthpur", 13.0285, 77.5409, "transit"),
    ("Silk Board Junction", "BTM / HSR", 12.9174, 77.6229, "landmark"),
    ("Marathahalli Bridge", "Marathahalli", 12.9569, 77.7011, "landmark"),
    ("Banashankari Temple", "Banashankari", 12.9255, 77.5468, "landmark"),
    ("Commercial Street", "Shivajinagar", 12.9824, 77.6082, "landmark"),
    ("UB City", "Vittal Mallya Road", 12.9719, 77.5963, "landmark"),
    ("Bangalore Palace", "Vasanth Nagar", 12.9988, 77.5921, "landmark"),
    ("Chinnaswamy Stadium", "MG Road", 12.9788, 77.5996, "landmark"),
    ("Fire Station Richmond Road", "Richmond Town", 12.9648, 77.6026, "fire"),
    ("Police Commissioner Office", "Infantry Road", 12.9806, 77.5953, "police"),
]

# Keep everything inside the supported Bengaluru box (config.BENGALURU_BOUNDS).
_ITEMS = [
    {"name": n, "area": a, "lat": lat, "lng": lng, "kind": k}
    for n, a, lat, lng, k in PLACES
    if 12.83 <= lat <= 13.14 and 77.45 <= lng <= 77.75
]


def all_places() -> List[dict]:
    return _ITEMS


_STOP_WORDS = {"a", "at", "around", "in", "near", "the", "to"}
_SYNONYMS = {
    "bangalore": "bengaluru",
    "bengaluru": "bangalore",
    "clinic": "hospital",
    "medical": "hospital",
    "metro": "transit",
    "railway": "transit",
    "station": "transit",
}


def _words(value: str) -> List[str]:
    return "".join(char.lower() if char.isalnum() else " " for char in value).split()


def _matches_term(term: str, words: List[str]) -> bool:
    for word in words:
        if word == term or word.startswith(term):
            return True
        if len(term) >= 5 and len(word) >= 5 and SequenceMatcher(None, term, word).ratio() >= 0.8:
            return True
    return False


def _searchable_words(place: dict) -> List[str]:
    words = _words(f"{place['name']} {place['area']} {place['kind']}")
    return words + [_SYNONYMS[word] for word in words if word in _SYNONYMS]


def search(q: str, limit: int = 6) -> List[dict]:
    query_words = [word for word in _words(q) if word not in _STOP_WORDS]
    if not query_words:
        return []
    terms = [_SYNONYMS.get(word, word) for word in query_words]
    scored = []
    for p in _ITEMS:
        words = _searchable_words(p)
        matches = [next((word for word in words if _matches_term(term, [word])), None) for term in terms]
        if any(match is None for match in matches):
            continue
        exact_phrase = " ".join(_words(f"{p['name']} {p['area']}")).find(" ".join(_words(q))) >= 0
        exact_words = sum(match == term for match, term in zip(matches, terms))
        starts_name = " ".join(_words(p["name"])).startswith(" ".join(_words(q)))
        score = (100 if exact_phrase else 0) + exact_words * 10 + (20 if starts_name else 0)
        scored.append((score, p))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [p for _, p in scored[:limit]]
