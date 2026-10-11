"""
Route generation + geometry helpers.

`generate_route_options` now calls the real Google Maps Directions API when
GOOGLE_MAPS_API_KEY is configured (see config.py), requesting alternative
routes biased to Bengaluru/India. Each Google route becomes route A/B/C with
a real road-following polyline, real distance, and a real duration-based ETA
(duration_in_traffic when Google returns it).

When OSRM is enabled (see config.py) it is used as an open-source,
OpenStreetMap-based provider: Google -> OSRM -> mock in "auto" mode. OSRM's
durations are free-flow (no live traffic).

If no API key is set, or the request fails for any reason (network, quota,
bad key, ZERO_RESULTS, etc.), it transparently falls back to the original
mock curve-generator below, so the app keeps working offline / with zero
setup. Every route carries an `is_mock` flag so the frontend/backend can
tell which source produced it (see RouteOption.is_mock).
"""
import logging
import math
from typing import List, Tuple

import requests

from . import config

logger = logging.getLogger("geoagentic.routing")

EARTH_R_KM = 6371.0


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(h))


def polyline_length_km(points: List[List[float]]) -> float:
    return sum(
        haversine_km(tuple(points[i]), tuple(points[i + 1]))
        for i in range(len(points) - 1)
    )


def _km_to_deg_scale(lat_deg: float):
    """Return (deg_per_km_lat, deg_per_km_lng) at a given latitude."""
    ky = 1 / 110.574
    kx = 1 / (111.320 * math.cos(math.radians(lat_deg)) or 1e-9)
    return ky, kx


def _offset_point(a, b, t, perp_frac_km, curve_bias_km=0.0):
    """Point at fraction t along a->b, displaced perpendicular by perp_frac_km
    (kilometres) of bulge. Perpendicular offset is computed in a local metric
    (km) frame and converted back to lat/lng degrees, so bulges stay a sane
    physical size regardless of latitude."""
    lat = a[0] + (b[0] - a[0]) * t
    lng = a[1] + (b[1] - a[1]) * t
    deg_per_km_lat, deg_per_km_lng = _km_to_deg_scale((a[0] + b[0]) / 2)

    # Direction a->b expressed in km-space to get a true perpendicular.
    dlat_km = (b[0] - a[0]) / deg_per_km_lat
    dlng_km = (b[1] - a[1]) / deg_per_km_lng
    seg_len_km = math.hypot(dlat_km, dlng_km) or 1e-9
    plat_km, plng_km = -dlng_km / seg_len_km, dlat_km / seg_len_km

    bulge_km = math.sin(t * math.pi) * perp_frac_km + curve_bias_km * t * (1 - t)
    lat += plat_km * bulge_km * deg_per_km_lat
    lng += plng_km * bulge_km * deg_per_km_lng
    return [lat, lng]


def _build_curve(origin, dest, perp_frac_km, n=10, curve_bias_km=0.0):
    return [_offset_point(origin, dest, i / n, perp_frac_km, curve_bias_km) for i in range(n + 1)]


# ---------------------------------------------------------------------------
# Real routing: Google Maps Directions API
# ---------------------------------------------------------------------------
def _decode_polyline(encoded: str) -> List[List[float]]:
    """Decode a Google encoded polyline string into [[lat, lng], ...].
    Standard Google polyline algorithm (precision 1e-5) — no extra
    dependency needed for this."""
    points = []
    index = lat = lng = 0
    length = len(encoded)

    while index < length:
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lng += delta
        points.append([lat / 1e5, lng / 1e5])
    return points


def _google_directions_routes(origin: Tuple[float, float], dest: Tuple[float, float]):
    """Call the Google Maps Directions API and return up to three route
    dicts (label/polyline/distance_km/base_eta_min/is_mock=False), or None
    if the API isn't configured, errors, or returns no routes."""
    if not config.GOOGLE_MAPS_ENABLED:
        return None

    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{dest[0]},{dest[1]}",
        "alternatives": "true",
        "mode": "driving",
        "region": config.GOOGLE_MAPS_REGION,
        "departure_time": "now",  # enables duration_in_traffic when available
        "key": config.GOOGLE_MAPS_API_KEY,
    }
    try:
        resp = requests.get(config.GOOGLE_DIRECTIONS_URL, params=params,
                             timeout=config.GOOGLE_MAPS_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Google Directions request failed, falling back to mock routes: %s", exc)
        return None

    status = data.get("status")
    if status != "OK":
        logger.warning("Google Directions returned status=%s (%s), falling back to mock routes",
                        status, data.get("error_message", ""))
        return None

    routes = []
    labels = ["A", "B", "C"]
    for gr, label in zip(data.get("routes", [])[:3], labels):
        legs = gr.get("legs", [])
        if not legs:
            continue
        leg = legs[0]
        overview = gr.get("overview_polyline", {}).get("points")
        if not overview:
            continue
        polyline = _decode_polyline(overview)
        distance_km = leg.get("distance", {}).get("value", 0) / 1000.0
        traffic_duration = leg.get("duration_in_traffic", {}).get("value")
        duration_s = traffic_duration or leg.get("duration", {}).get("value")
        if not duration_s:
            duration_s = leg.get("duration", {}).get("value", 0)
        base_eta_min = round(duration_s / 60.0, 2) if duration_s else round(
            (distance_km / 45.0) * 60, 2)
        routes.append({
            "label": label,
            "polyline": polyline,
            "distance_km": round(distance_km, 3),
            "base_eta_min": base_eta_min,
            "is_mock": False,
            "traffic_aware": bool(traffic_duration),
        })

    return routes or None


def _osrm_routes(origin: Tuple[float, float], dest: Tuple[float, float]):
    """Call the OSRM Route service (OpenStreetMap road network) and return up
    to three route dicts in the same shape as the Google helper, or None if
    OSRM is disabled, errors, or returns no routes.

    Note OSRM expects coordinates as lon,lat (the reverse of the rest of this
    codebase) and we ask for GeoJSON geometry so no polyline decoding is needed.
    """
    if not config.OSRM_ENABLED:
        return None

    coords = f"{origin[1]},{origin[0]};{dest[1]},{dest[0]}"
    url = f"{config.OSRM_BASE_URL}/route/v1/{config.OSRM_PROFILE}/{coords}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "alternatives": config.OSRM_ALTERNATIVES,
        "steps": "false",
    }
    try:
        resp = requests.get(url, params=params, timeout=config.OSRM_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OSRM request failed, falling back: %s", exc)
        return None

    if data.get("code") != "Ok":
        logger.warning("OSRM returned code=%s (%s), falling back",
                        data.get("code"), data.get("message", ""))
        return None

    routes = []
    for orr, label in zip(data.get("routes", [])[:3], ["A", "B", "C"]):
        coordinates = (orr.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) < 2:
            continue
        polyline = [[lat, lon] for lon, lat in coordinates]  # GeoJSON lon,lat -> lat,lng
        distance_km = orr.get("distance", 0) / 1000.0
        duration_s = orr.get("duration", 0)
        base_eta_min = round(duration_s / 60.0, 2) if duration_s else round(
            (distance_km / 45.0) * 60, 2)
        routes.append({
            "label": label,
            "polyline": polyline,
            "distance_km": round(distance_km, 3),
            "base_eta_min": base_eta_min,
            "is_mock": False,
            "traffic_aware": False,
        })
    return routes or None


def resolve_route_options(origin: Tuple[float, float], dest: Tuple[float, float],
                           avg_speed_kmh: float = 45.0):
    """Return (routes, provider) where provider is "google", "osrm" or "mock".

    Provider order follows config.ROUTING_PROVIDER:
      auto   -> Google (if key) -> OSRM -> mock
      google -> Google -> mock
      osrm   -> OSRM -> mock
      mock   -> mock
    """
    mode = config.ROUTING_PROVIDER
    if mode in ("auto", "google"):
        routes = _google_directions_routes(origin, dest)
        if routes:
            return routes, "google"
    if mode in ("auto", "osrm"):
        routes = _osrm_routes(origin, dest)
        if routes:
            return routes, "osrm"
    return _generate_mock_route_options(origin, dest, avg_speed_kmh=avg_speed_kmh), "mock"


def generate_route_options(origin: Tuple[float, float], dest: Tuple[float, float],
                            avg_speed_kmh: float = 45.0):
    """Return up to three route candidates for a mission.

    Tries the configured real providers (Google Directions, OSRM — see
    resolve_route_options) and falls back to the deterministic mock
    curve-generator otherwise or on any failure. Return shape is identical
    either way: a list of dicts with label/polyline/distance_km/
    base_eta_min/is_mock.
    """
    return resolve_route_options(origin, dest, avg_speed_kmh)[0]


def _generate_mock_route_options(origin: Tuple[float, float], dest: Tuple[float, float],
                                  avg_speed_kmh: float = 45.0):
    """Return three mock road-like route candidates: A (direct), B (east detour), C (west detour).
    Used when no Google Maps API key is configured, or the live API call fails."""
    direct_km = haversine_km(origin, dest)
    span = max(direct_km, 0.3)
    # Perpendicular displacement scaled to route length so detours look sensible
    # at both short (city block) and long (cross-town) distances.
    a_perp = span * 0.02
    b_perp = span * 0.12
    c_perp = -span * 0.16

    routes = [
        {"label": "A", "polyline": _build_curve(origin, dest, a_perp, curve_bias_km=span * 0.03)},
        {"label": "B", "polyline": _build_curve(origin, dest, b_perp, n=12, curve_bias_km=span * 0.10)},
        {"label": "C", "polyline": _build_curve(origin, dest, c_perp, n=12, curve_bias_km=-span * 0.06)},
    ]
    for r in routes:
        dist = polyline_length_km(r["polyline"])
        r["distance_km"] = round(dist, 3)
        r["base_eta_min"] = round((dist / max(avg_speed_kmh, 1)) * 60, 2)
        r["is_mock"] = True
        r["traffic_aware"] = False
    return routes


def point_to_segment_distance_km(p, a, b) -> float:
    """Approximate distance from point p to segment a-b, in km, using an
    equirectangular projection (fine for city-scale distances)."""
    lat0 = math.radians((a[0] + b[0]) / 2)
    kx = 111.320 * math.cos(lat0)  # km per degree longitude at this latitude
    ky = 110.574                    # km per degree latitude

    def proj(pt):
        return (pt[1] * kx, pt[0] * ky)  # (x=lng*kx, y=lat*ky)

    px, py = proj(p)
    ax, ay = proj(a)
    bx, by = proj(b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def point_to_polyline_distance_km(p, polyline: List[List[float]]) -> float:
    return min(
        point_to_segment_distance_km(p, polyline[i], polyline[i + 1])
        for i in range(len(polyline) - 1)
    )


def point_on_polyline_at_fraction(polyline: List[List[float]], frac: float) -> List[float]:
    """Walk the polyline and return the [lat,lng] at fraction (0..1) of its total length."""
    frac = max(0.0, min(1.0, frac))
    total = polyline_length_km(polyline)
    if total <= 0:
        return polyline[-1]
    target = total * frac
    covered = 0.0
    for i in range(len(polyline) - 1):
        seg = haversine_km(tuple(polyline[i]), tuple(polyline[i + 1]))
        if covered + seg >= target or i == len(polyline) - 2:
            remain = target - covered
            t = 0 if seg == 0 else max(0.0, min(1.0, remain / seg))
            lat = polyline[i][0] + (polyline[i + 1][0] - polyline[i][0]) * t
            lng = polyline[i][1] + (polyline[i + 1][1] - polyline[i][1]) * t
            return [lat, lng]
        covered += seg
    return polyline[-1]
