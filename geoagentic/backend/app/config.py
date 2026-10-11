"""
Centralized configuration for external integrations.

Loads `.env` (via python-dotenv) once, on import, so every module can just
`from .config import ...` instead of re-reading environment variables.

Five integrations live here:

1. Google Maps Directions API — real routing for Bengaluru (and anywhere
   else). Set GOOGLE_MAPS_API_KEY. Without a key, `routing.py` transparently
   falls back to the original mock curve-generator, so the app still runs
   out of the box.

2. Anthropic Claude API — real LLM-generated dispatcher explanations. Set
   ANTHROPIC_API_KEY. Without a key, `decision_engine.explain()` falls back
   to the original templated sentence-join, so the app still runs offline.

3. OSRM (Open Source Routing Machine) — free, open-source routing on
   OpenStreetMap road data, no API key. OSRM's Route service returns the
   fastest route for a routing profile plus alternatives. It has NO live
   traffic, so incident delay still comes from the decision engine.
   Provider order is controlled by ROUTING_PROVIDER (auto|google|osrm|mock).

4. TomTom — server-side map tiles and live route-scoped traffic monitoring.
   Set TOMTOM_API_KEY. Without a key, monitoring reports unavailable and
   never clears incidents based on stale data.

5. LangGraph — the multi-agent orchestration layer (app/agents.py). It is a
   plain Python dependency, so there is no key; AGENTS_ENABLED just reports
   whether the package is importable.

Nothing here raises at import time if a key is missing — every caller is
expected to check `*_ENABLED` (or just try/except) and degrade gracefully.
This keeps the "zero setup" quick start in the README true when API keys are unset.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # no-op if there's no .env file; real env vars always win

# ---------------------------------------------------------------------------
# Google Maps Directions API (real routing)
# ---------------------------------------------------------------------------
GOOGLE_MAPS_API_KEY = (
    os.getenv("GOOGLE_MAPS_API_KEY")
    or os.getenv("ROUTE_API")
    or os.getenv("ROUTE_KEY")
    or ""
).strip()
GOOGLE_MAPS_ENABLED = bool(GOOGLE_MAPS_API_KEY)

# Bengaluru city-center coordinates, used as the default `region`/location
# bias for Directions API calls so ambiguous road names resolve locally
# instead of to a same-named street elsewhere in the world.
BENGALURU_CENTER = (12.9716, 77.5946)
GOOGLE_MAPS_REGION = os.getenv("GOOGLE_MAPS_REGION", "in")  # ccTLD bias, India
GOOGLE_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
GOOGLE_MAPS_TIMEOUT_S = float(os.getenv("GOOGLE_MAPS_TIMEOUT_S", "3"))

# ---------------------------------------------------------------------------
# Routing provider selection + OSRM (OpenStreetMap-based routing)
# ---------------------------------------------------------------------------
# auto   -> Google (if key set) -> OSRM -> mock curves   (default)
# google -> Google only (falls back to mock)
# osrm   -> OSRM only   (falls back to mock)
# mock   -> deterministic mock curves only (fully offline)
ROUTING_PROVIDER = os.getenv("ROUTING_PROVIDER", "auto").strip().lower()
if ROUTING_PROVIDER not in ("auto", "google", "osrm", "mock"):
    ROUTING_PROVIDER = "auto"

# The public demo server is fair-use / testing only (driving profile, no
# uptime guarantee). For real experiments self-host OSRM on a Karnataka OSM
# extract and point OSRM_BASE_URL at it, e.g. http://localhost:5000
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
OSRM_PROFILE = os.getenv("OSRM_PROFILE", "driving")
OSRM_TIMEOUT_S = float(os.getenv("OSRM_TIMEOUT_S", "2.5"))
# "true" = OSRM's default single alternative; an integer (e.g. "3") asks for
# up to N alternatives on OSRM >= 5.? builds that support it.
OSRM_ALTERNATIVES = os.getenv("OSRM_ALTERNATIVES", "true")
OSRM_ENABLED = (
    os.getenv("OSRM_ENABLED", "true").strip().lower() not in ("0", "false", "no")
    and ROUTING_PROVIDER in ("auto", "osrm")
)

# ---------------------------------------------------------------------------
# LangGraph (multi-agent orchestration)
# ---------------------------------------------------------------------------
try:  # pragma: no cover - depends on the environment
    import langgraph  # noqa: F401
    AGENTS_ENABLED = True
except ImportError:  # pragma: no cover
    AGENTS_ENABLED = False

# Delay (minutes) on the active route at which the Resource Agent starts
# considering backup for ambulances even when no better route exists.
AGENT_BACKUP_DELAY_MIN = float(os.getenv("AGENT_BACKUP_DELAY_MIN", "10"))
# Distance (metres) from the planned route beyond which the Trajectory
# Agent flags a deviation.
AGENT_DEVIATION_THRESHOLD_M = float(os.getenv("AGENT_DEVIATION_THRESHOLD_M", "250"))

# ---------------------------------------------------------------------------
# Bengaluru urban scope
# ---------------------------------------------------------------------------
# This demo is scoped to Bengaluru urban limits only (BBMP area + a small
# buffer). All vehicle/mission/incident coordinates are validated against
# this box (see schemas.py) and the frontend map is bounded to it too, so
# the whole app — seed data, dispatch form, Google routing, LLM context —
# stays inside one consistent city rather than silently accepting a
# coordinate from anywhere in the world.
BENGALURU_BOUNDS = {
    "min_lat": 12.83,
    "max_lat": 13.14,
    "min_lng": 77.45,
    "max_lng": 77.75,
}


def within_bengaluru(lat: float, lng: float) -> bool:
    b = BENGALURU_BOUNDS
    return b["min_lat"] <= lat <= b["max_lat"] and b["min_lng"] <= lng <= b["max_lng"]

# ---------------------------------------------------------------------------
# Anthropic Claude API (real LLM explanations)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_ENABLED = bool(ANTHROPIC_API_KEY)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_TIMEOUT_S = float(os.getenv("ANTHROPIC_TIMEOUT_S", "8"))
ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "200"))

# ---------------------------------------------------------------------------
# TomTom (map tiles, traffic-flow overlay, live traffic incidents)
# ---------------------------------------------------------------------------
# The key stays on the server. The browser never sees it: the frontend calls
# /api/tiles/*, /api/live/incidents and /api/geocode, and live.py adds the key.
TOMTOM_API_KEY = (
    os.getenv("TOMTOM_API_KEY")
    or os.getenv("TOM_API")
    or os.getenv("TOMTOM_KEY")
    or ""
).strip()
TOMTOM_ENABLED = bool(TOMTOM_API_KEY)
TOMTOM_TIMEOUT_S = float(os.getenv("TOMTOM_TIMEOUT_S", "8"))
TOMTOM_CACHE_TTL_S = float(os.getenv("TOMTOM_CACHE_TTL_S", "45"))
TOMTOM_MONITOR_INTERVAL_S = float(os.getenv("TOMTOM_MONITOR_INTERVAL_S", "45"))
TOMTOM_ROUTE_PROXIMITY_M = float(os.getenv("TOMTOM_ROUTE_PROXIMITY_M", "35"))
TOMTOM_CLOSURE_MATCH_M = float(os.getenv("TOMTOM_CLOSURE_MATCH_M", "8"))
TOMTOM_REROUTE_SAVING_MIN = float(os.getenv("TOMTOM_REROUTE_SAVING_MIN", "2"))
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
