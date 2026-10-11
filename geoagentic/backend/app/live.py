"""
Server-side proxies that let the React frontend use the real API keys
without ever receiving them.

  GET /api/tiles/{layer}/{z}/{x}/{y}.png   TomTom base map / traffic-flow tiles
  GET /api/live/incidents                  live TomTom incidents (Bengaluru box)
  GET /api/geocode?q=...                   Nominatim search, Bengaluru-bounded

Why a proxy instead of VITE_* keys: anything in a Vite env var is compiled
into the JS bundle and readable by every visitor.
"""
import time
import threading
import requests
from fastapi import APIRouter, HTTPException, Query, Response

from . import config
from . import places as builtin_places

router = APIRouter(prefix="/api")

_TILE_URLS = {
    "base": "https://api.tomtom.com/map/1/tile/basic/main/{z}/{x}/{y}.png",
    "flow": "https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{z}/{x}/{y}.png",
}


@router.get("/tiles/{layer}/{z}/{x}/{y}.png")
def tile(layer: str, z: int, x: int, y: int):
    if layer not in _TILE_URLS:
        raise HTTPException(404, "unknown tile layer")
    if not config.TOMTOM_ENABLED:
        raise HTTPException(503, "TomTom key not configured")
    if not (0 <= z <= 22):
        raise HTTPException(422, "bad zoom")
    url = _TILE_URLS[layer].format(z=z, x=x, y=y)
    try:
        r = requests.get(url, params={"key": config.TOMTOM_API_KEY, "tileSize": 256},
                         timeout=config.TOMTOM_TIMEOUT_S)
    except requests.RequestException as exc:
        raise HTTPException(502, f"tile upstream error: {exc.__class__.__name__}")
    if r.status_code != 200:
        raise HTTPException(r.status_code if r.status_code in (403, 404, 429) else 502, "tile upstream error")
    ttl = 60 if layer == "flow" else 86400  # flow is live; base map is static
    return Response(r.content, media_type="image/png", headers={"Cache-Control": f"public, max-age={ttl}"})


# TomTom iconCategory -> the incident types the frontend already styles
_CATEGORY = {1: "accident", 6: "traffic", 7: "roadblock", 8: "roadblock", 9: "roadblock",
             11: "flood", 4: "flood", 3: "hazard", 14: "hazard", 2: "hazard", 5: "hazard"}
_SEVERITY = {0: "low", 1: "low", 2: "medium", 3: "high", 4: "high"}
_cache = {"t": 0.0, "data": []}
_cache_lock = threading.Lock()
_CACHE_TTL_S = config.TOMTOM_CACHE_TTL_S


def _mid(geom):
    points = []

    def collect(value):
        if (isinstance(value, (list, tuple)) and len(value) >= 2
                and isinstance(value[0], (int, float))
                and isinstance(value[1], (int, float))):
            points.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect((geom or {}).get("coordinates") or [])
    if points:
        p = points[len(points) // 2]
        return float(p[1]), float(p[0])
    return None, None


def fetch_live_incidents(force_refresh=False):
    if not config.TOMTOM_ENABLED:
        return {"enabled": False, "status": "unavailable",
                "error": "TomTom API key is not configured", "incidents": []}
    with _cache_lock:
        cached_at = _cache["t"]
        cached_data = list(_cache["data"])
    if not force_refresh and time.time() - cached_at < _CACHE_TTL_S:
        return {"enabled": True, "status": "live", "incidents": cached_data, "cached": True}
    b = config.BENGALURU_BOUNDS
    params = {
        "key": config.TOMTOM_API_KEY,
        "bbox": f"{b['min_lng']},{b['min_lat']},{b['max_lng']},{b['max_lat']}",
        "fields": "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,"
                  "magnitudeOfDelay,events{description},from,to,delay,roadNumbers}}}",
        "language": "en-GB",
        "timeValidityFilter": "present",
    }
    try:
        r = requests.get("https://api.tomtom.com/traffic/services/5/incidentDetails",
                         params=params, timeout=config.TOMTOM_TIMEOUT_S)
        r.raise_for_status()
        raw = r.json().get("incidents", [])
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        status_code = response.status_code if response is not None else None
        error = ("invalid_key" if status_code in (401, 403)
                 else "rate_limited" if status_code == 429
                 else "timeout" if isinstance(exc, requests.Timeout)
                 else "upstream_error")
        return {"enabled": True, "status": "stale" if cached_data else "unavailable",
                "error": error, "incidents": cached_data, "checked_at": time.time()}
    except (ValueError, AttributeError):
        return {"enabled": True, "status": "stale" if cached_data else "unavailable",
                "error": "invalid_response", "incidents": cached_data, "checked_at": time.time()}

    if not isinstance(raw, list):
        return {"enabled": True, "status": "stale" if cached_data else "unavailable",
                "error": "invalid_response", "incidents": cached_data, "checked_at": time.time()}
    out = []
    for i, inc in enumerate(raw):
        p = inc.get("properties", {})
        geometry = inc.get("geometry", {})
        lat, lng = _mid(geometry)
        if lat is None:
            continue
        ev = (p.get("events") or [{}])[0].get("description")
        external_id = p.get("id") or inc.get("id")
        if not external_id:
            continue
        out.append({
            "id": str(external_id),
            "external_id": str(external_id),
            "lat": lat, "lng": lng,
            "incident_type": _CATEGORY.get(p.get("iconCategory"), "traffic"),
            "severity": _SEVERITY.get(p.get("magnitudeOfDelay", 0), "low"),
            "description": ev or "TomTom traffic incident",
            "from": p.get("from"), "to": p.get("to"),
            "delay_s": p.get("delay"),
            "icon_category": p.get("iconCategory"),
            "magnitude_of_delay": p.get("magnitudeOfDelay"),
            "road_numbers": p.get("roadNumbers") or [],
            "geometry": geometry,
        })
    checked_at = time.time()
    with _cache_lock:
        _cache.update(t=checked_at, data=out)
    return {"enabled": True, "status": "live", "incidents": out, "checked_at": checked_at}


@router.get("/live/incidents")
def live_incidents():
    data = fetch_live_incidents()
    if data["status"] == "unavailable" and data.get("error") == "invalid_response":
        raise HTTPException(502, "TomTom returned an invalid response")
    return data


@router.get("/places")
def places():
    """Built-in Bengaluru landmarks / hospitals / stations (always available)."""
    return {"results": [dict(p, source="builtin") for p in builtin_places.all_places()]}


def _in_blr(lat, lng):
    return config.within_bengaluru(lat, lng)


@router.get("/geocode")
def geocode(q: str = Query(..., min_length=2, max_length=120)):
    """Place search. Built-in places are returned first (instant, offline);
    Nominatim results are appended when reachable. A Nominatim failure is
    NOT an error any more - the user still gets the built-in matches."""
    results = [
        {"name": f"{p['name']}, {p['area']}", "lat": p["lat"], "lng": p["lng"], "source": "builtin"}
        for p in builtin_places.search(q)
    ]
    b = config.BENGALURU_BOUNDS
    try:
        r = requests.get(config.NOMINATIM_URL, params={
            "q": f"{q}, Bengaluru", "format": "jsonv2", "limit": 5, "countrycodes": "in", "bounded": 1,
            "viewbox": f"{b['min_lng']},{b['max_lat']},{b['max_lng']},{b['min_lat']}",
        }, headers={"User-Agent": "GeoAgentic-hackathon/0.3 (local dev)"}, timeout=4)
        r.raise_for_status()
        for x in r.json():
            lat, lng = float(x["lat"]), float(x["lon"])
            if _in_blr(lat, lng):
                results.append({"name": x["display_name"], "lat": lat, "lng": lng, "source": "osm"})
    except (requests.RequestException, ValueError, KeyError):
        pass  # offline / blocked / rate-limited: built-in matches are enough
    return {"results": results[:8]}
