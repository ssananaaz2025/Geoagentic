"""Route-scoped TomTom incident monitoring and revalidation."""
import hashlib
import json
import logging
import math
import time
from typing import List

from sqlalchemy.orm import Session

from . import config, models
from .live import fetch_live_incidents
from .routing import (
    point_on_polyline_at_fraction,
    point_to_polyline_distance_km,
    resolve_route_options,
)

logger = logging.getLogger("geoagentic.traffic_monitor")
_SEVERITY_DELAY_MIN = {"low": 4.0, "medium": 9.0, "high": 16.0}


def _coordinates_to_lines(geometry) -> List[List[List[float]]]:
    if not isinstance(geometry, dict):
        return []
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if kind == "Point":
        lines = [[coordinates]] if isinstance(coordinates, (list, tuple)) else []
    elif kind == "LineString":
        lines = [coordinates] if isinstance(coordinates, list) else []
    elif kind == "MultiLineString":
        lines = coordinates if isinstance(coordinates, list) else []
    else:
        return []

    valid = []
    for line in lines:
        points = []
        for point in line:
            if (isinstance(point, (list, tuple)) and len(point) >= 2
                    and isinstance(point[0], (int, float))
                    and isinstance(point[1], (int, float))
                    and math.isfinite(point[0]) and math.isfinite(point[1])
                    and config.within_bengaluru(float(point[1]), float(point[0]))):
                points.append([float(point[1]), float(point[0])])
        if points:
            valid.append(points)
    return valid


def _segments_intersect(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p, q, r):
        return (min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
                and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9)

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return (
        (abs(o1) < 1e-9 and on_segment(a, c, b))
        or (abs(o2) < 1e-9 and on_segment(a, d, b))
        or (abs(o3) < 1e-9 and on_segment(c, a, d))
        or (abs(o4) < 1e-9 and on_segment(c, b, d))
    )


def _segment_distance_km(a, b, c, d):
    lat0 = math.radians((a[0] + b[0] + c[0] + d[0]) / 4)
    x_scale, y_scale = 111.320 * math.cos(lat0), 110.574

    def project(point):
        return point[1] * x_scale, point[0] * y_scale

    pa, pb, pc, pd = map(project, (a, b, c, d))
    if _segments_intersect(pa, pb, pc, pd):
        return 0.0

    def point_segment_distance(p, u, v):
        dx, dy = v[0] - u[0], v[1] - u[1]
        denominator = dx * dx + dy * dy
        t = 0.0 if denominator == 0 else max(
            0.0, min(1.0, ((p[0] - u[0]) * dx + (p[1] - u[1]) * dy) / denominator)
        )
        return math.hypot(p[0] - (u[0] + t * dx), p[1] - (u[1] + t * dy))

    return min(
        point_segment_distance(pa, pc, pd), point_segment_distance(pb, pc, pd),
        point_segment_distance(pc, pa, pb), point_segment_distance(pd, pa, pb),
    )


def geometry_distance_m(route_polyline, incident_geometry, fallback_point=None):
    if not isinstance(route_polyline, list) or len(route_polyline) < 2:
        return None
    lines = _coordinates_to_lines(incident_geometry)
    if not lines and fallback_point:
        lat, lng = fallback_point
        if config.within_bengaluru(lat, lng):
            lines = [[[lat, lng]]]
    if not lines:
        return None

    best_km = float("inf")
    for line in lines:
        if len(line) == 1:
            best_km = min(best_km, point_to_polyline_distance_km(line[0], route_polyline))
            continue
        for i in range(len(line) - 1):
            for j in range(len(route_polyline) - 1):
                best_km = min(
                    best_km,
                    _segment_distance_km(line[i], line[i + 1], route_polyline[j], route_polyline[j + 1]),
                )
    return best_km * 1000 if math.isfinite(best_km) else None


def _remaining_polyline(route, progress):
    points = route.polyline or []
    if len(points) < 2:
        return points
    start = point_on_polyline_at_fraction(points, progress or 0)
    prefix = []
    cumulative = 0.0
    from .routing import haversine_km

    total = sum(haversine_km(tuple(points[i]), tuple(points[i + 1])) for i in range(len(points) - 1))
    target = total * max(0.0, min(1.0, progress or 0))
    for i in range(len(points) - 1):
        segment = haversine_km(tuple(points[i]), tuple(points[i + 1]))
        if cumulative + segment >= target:
            prefix = [start] + points[i + 1:]
            break
        cumulative += segment
    return prefix if len(prefix) >= 2 else [start, points[-1]]


def _incident_is_closure(incident):
    return incident.get("icon_category") == 8


def _delay_min(incident):
    delay_s = incident.get("delay_s")
    if isinstance(delay_s, (int, float)) and delay_s > 0:
        return min(float(delay_s) / 60, 120.0)
    return _SEVERITY_DELAY_MIN.get(incident.get("severity"), 9.0)


def _route_incidents(route_polyline, incidents):
    matches = []
    for incident in incidents:
        distance_m = geometry_distance_m(
            route_polyline, incident.get("geometry"),
            (incident["lat"], incident["lng"]) if incident.get("lat") is not None else None,
        )
        if distance_m is None or distance_m > config.TOMTOM_ROUTE_PROXIMITY_M:
            continue
        confirmed_block = (
            _incident_is_closure(incident)
            and distance_m <= config.TOMTOM_CLOSURE_MATCH_M
            and incident.get("geometry", {}).get("type") in ("LineString", "MultiLineString")
        )
        matches.append({**incident, "distance_m": round(distance_m, 1),
                        "confirmed_block": confirmed_block})
    return matches


def _fingerprint(incident):
    payload = {
        "type": incident.get("incident_type"),
        "severity": incident.get("severity"),
        "delay_s": incident.get("delay_s"),
        "description": incident.get("description"),
        "icon_category": incident.get("icon_category"),
        "geometry": incident.get("geometry"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _monitor_state(db, mission_id):
    state = db.query(models.TrafficMonitorState).get(mission_id)
    if not state:
        state = models.TrafficMonitorState(mission_id=mission_id, status="pending")
        db.add(state)
        db.flush()
    return state


def _persist_relevant_incidents(db, mission, route, relevant, checked_at):
    ids = {item["external_id"] for item in relevant}
    changed = []
    for item in relevant:
        record = db.query(models.Incident).filter_by(
            mission_id=mission.id, source="tomtom", external_id=item["external_id"]
        ).first()
        fingerprint = _fingerprint(item)
        is_new = record is None
        materially_changed = is_new or (record.geometry != item["geometry"]
                                        or record.description != item["description"]
                                        or record.severity != item["severity"]
                                        or record.delay_s != item.get("delay_s")
                                        or record.icon_category != item.get("icon_category"))
        if record is None:
            record = models.Incident(
                mission_id=mission.id,
                incident_type=item["incident_type"],
                lat=item["lat"], lng=item["lng"],
                source="tomtom", external_id=item["external_id"],
                first_seen_at=checked_at,
            )
            db.add(record)
        record.incident_type = item["incident_type"]
        record.lat, record.lng = item["lat"], item["lng"]
        record.geometry = item["geometry"]
        record.description = item["description"]
        record.severity = item["severity"]
        record.delay_s = item.get("delay_s")
        record.icon_category = item.get("icon_category")
        record.source_data = {
            "from": item.get("from"), "to": item.get("to"),
            "road_numbers": item.get("road_numbers") or [],
            "magnitude_of_delay": item.get("magnitude_of_delay"),
            "fingerprint": fingerprint,
            "distance_to_route_m": item["distance_m"],
            "confirmed_block": item["confirmed_block"],
        }
        record.affected_route_id = route.id
        record.status = "active"
        record.freshness = "current"
        record.last_seen_at = checked_at
        record.last_checked_at = checked_at
        if materially_changed:
            changed.append((record, is_new))

    # A successful response is the only evidence that an earlier TomTom
    # incident has cleared. Outages never resolve cached incident records.
    previous = db.query(models.Incident).filter_by(
        mission_id=mission.id, source="tomtom", status="active"
    ).all()
    cleared = []
    for record in previous:
        if record.external_id not in ids:
            record.status = "resolved"
            record.freshness = "resolved"
            record.last_checked_at = checked_at
            cleared.append(record)
    return changed, cleared


def _create_route_records(db, mission, options, route_matches, sequence, provider):
    persisted = []
    for index, option in enumerate(options):
        matches = route_matches[index]
        closed = any(match["confirmed_block"] for match in matches)
        affected = bool(matches)
        route = models.RouteOption(
            mission_id=mission.id,
            label=f"TT{sequence}-{option['label']}",
            polyline=option["polyline"],
            distance_km=option["distance_km"],
            base_eta_min=option["base_eta_min"],
            delay_min=0.0,
            risk_score=0.0,
            status="blocked" if closed else "available",
            is_mock=option.get("is_mock", True),
            traffic_aware=option.get("traffic_aware", False),
            traffic_status="affected" if affected else "clear",
        )
        route.delay_min = 0.0 if route.traffic_aware else max(
            (_delay_min(match) for match in matches
             if match["incident_type"] == "traffic"), default=0.0
        )
        route.risk_score = max(
            (0.8 if match["confirmed_block"] else
             0.35 if match["severity"] == "high" else
             0.2 if match["severity"] == "medium" else 0.1 for match in matches),
            default=0.0,
        )
        route._monitor_provider = provider
        db.add(route)
        persisted.append(route)
    db.flush()
    return persisted


def _recommendation_exists(db, mission_id, incident_id, fingerprint):
    pending = db.query(models.Recommendation).filter_by(
        mission_id=mission_id, incident_id=incident_id, status="pending"
    ).all()
    return any((recommendation.extra or {}).get("incident_fingerprint") == fingerprint
               for recommendation in pending)


def _same_road_geometry(candidate, active_remainder):
    if len(candidate) < 2 or len(active_remainder) < 2:
        return False
    distances = [
        point_to_polyline_distance_km(point, active_remainder) * 1000
        for point in candidate[::max(1, len(candidate) // 20)]
    ]
    return distances and max(distances) <= 20.0


def _emit_route_recommendation(db, mission, vehicle, active_route, event_record,
                               route_options, route_matches, provider, checked_at,
                               active_matches, active_remainder, sequence):
    remaining_eta = max(
        0.0, active_route.base_eta_min * (1 - (vehicle.route_progress or 0))
    )
    active_delay = 0.0 if active_route.traffic_aware else max(
        (_delay_min(item) for item in active_matches if item["incident_type"] == "traffic"),
        default=0.0,
    )
    active_closed = (
        active_route.status == "blocked"
        or any(item["confirmed_block"] for item in active_matches)
    )
    route_a_eta = remaining_eta + active_delay
    candidates = []

    if provider != "mock":
        matches_by_option = route_matches
        candidate_records = _create_route_records(
            db, mission, route_options, matches_by_option, sequence, provider
        )
        for record, option, matches in zip(candidate_records, route_options, matches_by_option):
            if _same_road_geometry(option.get("polyline", []), active_remainder):
                record.status = "available"
                record.traffic_status = "affected" if matches else "clear"
                continue
            if record.status == "blocked":
                continue
            eta = option["base_eta_min"] + record.delay_min
            score = eta + record.risk_score * 6.0
            candidates.append((score, eta, record))
    best = min(candidates, default=None, key=lambda value: value[0])
    active_score = route_a_eta + (0.8 * 6.0 if active_closed else 0.0)
    saving = active_score - best[0] if best else 0.0
    should_reroute = bool(best and (active_closed or saving >= config.TOMTOM_REROUTE_SAVING_MIN))
    selected = best[2] if should_reroute else None
    action = "reroute" if selected else "hold"
    if provider == "mock":
        reason = (
            "A TomTom incident affects the active route, but no real alternative route provider "
            "is available. No unverified mock route was offered."
        )
        alternative_eta = None
        alternative_distance = None
        saved_min = None
    elif selected:
        reason = (
            f"TomTom {event_record.incident_type} affects active route {active_route.label} "
            f"({event_record.description}). Route {selected.label} was checked against current "
            f"TomTom incidents and estimates {best[1]:.1f} min, saving about {max(0.0, saving):.1f} min."
        )
        alternative_eta = round(best[1], 1)
        alternative_distance = round(selected.distance_km, 2)
        saved_min = round(max(0.0, saving), 1)
    else:
        reason = (
            f"TomTom {event_record.incident_type} affects active route {active_route.label}. "
            "No current alternative was validated as meaningfully better; keep the current route "
            "under dispatcher review."
        )
        alternative_eta = None
        alternative_distance = None
        saved_min = None

    fingerprint = (event_record.source_data or {}).get("fingerprint", "")
    if _recommendation_exists(db, mission.id, event_record.id, fingerprint):
        return None

    explanation_facts = {
        "vehicle": vehicle.callsign,
        "incident": {
            "source": "TomTom",
            "type": event_record.incident_type.replace("_", " "),
            "description": event_record.description or event_record.incident_type.replace("_", " "),
            "status": "active",
            "freshness": event_record.freshness,
            "last_checked_at": checked_at,
            "affects_current_route": True,
        },
        "current_route": {
            "label": active_route.label,
            "eta_min": round(route_a_eta, 1),
            "status": "blocked" if active_closed else "affected",
        },
        "alternative_route": ({
            "label": selected.label,
            "eta_min": alternative_eta,
            "status": "affected" if selected.traffic_status == "affected" else "no_reported_incident",
            "available": True,
            "blocked": False,
            "verification": "checked against latest TomTom incidents",
        } if selected else {"available": False}),
        "time_saved_min": saved_min,
        "action": action,
        "impact": (
            f"A reported closure affects {active_route.label}."
            if active_closed else
            f"A TomTom {event_record.incident_type} may delay travel on {active_route.label}."
        ),
        "approval_status": "waiting",
        "traffic_status": "current",
    }
    from . import decision_engine
    reason = decision_engine.explain(explanation_facts)

    recommendation = models.Recommendation(
        mission_id=mission.id,
        incident_id=event_record.id,
        recommended_route_id=selected.id if selected else None,
        action=action,
        reason=reason,
        confidence=0.88 if selected else 0.65,
        extra={
            "monitor_source": "tomtom",
            "incident_fingerprint": fingerprint,
            "explanation_facts": explanation_facts,
            "vehicle_id": vehicle.id,
            "callsign": vehicle.callsign,
            "mission_destination": {"lat": mission.dest_lat, "lng": mission.dest_lng},
            "incident": {
                "external_id": event_record.external_id,
                "type": event_record.incident_type,
                "description": event_record.description,
                "source": "TomTom",
                "lat": event_record.lat,
                "lng": event_record.lng,
                "severity": event_record.severity,
                "last_checked_at": checked_at,
                "distance_to_route_m": (event_record.source_data or {}).get("distance_to_route_m"),
            },
            "route_a": {"id": active_route.id, "label": active_route.label,
                        "eta_min": round(route_a_eta, 1),
                        "status": "blocked" if active_closed else "affected"},
            "route_b": ({
                "id": selected.id, "label": selected.label,
                "eta_min": alternative_eta, "distance_km": alternative_distance,
                "status": "checked_clear" if selected.traffic_status == "clear" else "checked_affected",
                "provider": provider,
            } if selected else None),
            "estimated_saving_min": saved_min,
            "validation_status": "live" if provider != "mock" else "unavailable",
            "alternatives_checked": len(route_options) if provider != "mock" else 0,
            "route_evaluations": [{
                "label": option.get("label"),
                "eta_min": round(option["base_eta_min"] + (
                    0.0 if option.get("traffic_aware") else max(
                        (_delay_min(item) for item in matches
                         if item["incident_type"] == "traffic"), default=0.0)
                ), 1),
                "distance_km": round(option["distance_km"], 2),
                "status": ("blocked" if any(item["confirmed_block"] for item in matches)
                           else "affected" if matches else "no_reported_incident"),
                "traffic_aware_eta": bool(option.get("traffic_aware")),
            } for option, matches in zip(route_options, route_matches)],
            "agent_trace": [
                {"agent": "observe", "message": f"Fresh TomTom response evaluated for mission {mission.id}."},
                {"agent": "incident", "message": f"{event_record.incident_type} geometry matched route {active_route.label}."},
                {"agent": "eta_impact", "message": f"Active-route ETA estimated at {route_a_eta:.1f} min."},
                {"agent": "route", "message": f"Checked {len(route_options) if provider != 'mock' else 0} real alternatives using {provider}."},
                {"agent": "recommend", "message": reason},
            ],
        },
    )
    db.add(recommendation)
    db.add(models.TimelineEvent(
        mission_id=mission.id,
        vehicle_id=vehicle.id,
        event_type="recommendation",
        message=(
            f"{vehicle.callsign}: TomTom {event_record.incident_type} affects route "
            f"{active_route.label}. " + (
                f"{selected.label} estimated to save {saved_min:.1f} min; review the alternative."
                if selected else "No validated alternative is currently available."
            )
        ),
    ))
    return recommendation


def monitor_mission(db: Session, mission: models.Mission, response, checked_at=None):
    checked_at = checked_at or time.time()
    state = _monitor_state(db, mission.id)
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
    active_route = db.query(models.RouteOption).get(vehicle.active_route_id) if vehicle.active_route_id else None
    state.last_checked_at = checked_at

    if response.get("status") != "live":
        state.status = response.get("status", "unavailable")
        state.last_error = response.get("error") or "TomTom response is not fresh"
        state.affected = False
        stale_at = checked_at
        for incident in db.query(models.Incident).filter_by(
            mission_id=mission.id, source="tomtom", status="active"
        ).all():
            incident.freshness = "stale"
            incident.last_checked_at = stale_at
        if active_route and active_route.traffic_status != "affected":
            active_route.traffic_status = "unverified"
        return []

    state.status = "live"
    state.last_error = None
    if mission.status != "active" or not active_route:
        state.route_id = active_route.id if active_route else None
        state.affected = False
        return []

    external_incidents = response.get("incidents") or []
    remaining = _remaining_polyline(active_route, vehicle.route_progress)
    active_matches = _route_incidents(remaining, external_incidents)
    active_route.traffic_status = "affected" if active_matches else "clear"
    state.route_id = active_route.id
    state.provider = None
    state.affected = bool(active_matches)

    changed, cleared = _persist_relevant_incidents(
        db, mission, active_route, active_matches, checked_at
    )
    for record in cleared:
        db.add(models.TimelineEvent(
            mission_id=mission.id, vehicle_id=vehicle.id, event_type="traffic",
            message=f"TomTom incident {record.external_id} cleared from active route {active_route.label}.",
        ))

    if not active_matches:
        return []

    options, provider = resolve_route_options(
        (vehicle.lat, vehicle.lng), (mission.dest_lat, mission.dest_lng),
        avg_speed_kmh=vehicle.speed_kmh or vehicle.base_speed_kmh or 45.0,
    )
    state.provider = provider
    matches = [_route_incidents(option.get("polyline", []), external_incidents) for option in options]
    active_remainder = _remaining_polyline(active_route, vehicle.route_progress)
    created = []
    for record, is_new in changed:
        item = next((item for item in active_matches
                     if item["external_id"] == record.external_id), None)
        if not item:
            continue
        recommendation = _emit_route_recommendation(
            db, mission, vehicle, active_route, record,
            options, matches, provider, checked_at, active_matches,
            active_remainder, sequence=int(checked_at) % 100000,
        )
        if recommendation:
            created.append(recommendation)
            db.add(models.TimelineEvent(
                mission_id=mission.id, vehicle_id=vehicle.id, event_type="traffic",
                message=(
                    f"TomTom {'new incident' if is_new else 'incident update'} matched route "
                    f"{active_route.label} within "
                    f"{(record.source_data or {}).get('distance_to_route_m', 0):.0f} m."
                ),
            ))
    return created


def monitor_active_missions(db: Session, response=None, checked_at=None):
    response = response or fetch_live_incidents()
    checked_at = checked_at or response.get("checked_at") or time.time()
    missions = db.query(models.Mission).filter_by(status="active").all()
    recommendations = []
    for mission in missions:
        recommendations.extend(monitor_mission(db, mission, response, checked_at))
    db.commit()
    for mission_id in {recommendation.mission_id for recommendation in recommendations}:
        start_agent_workflow(mission_id)
    return {"status": response.get("status", "unavailable"),
            "checked_at": checked_at, "missions_checked": len(missions),
            "recommendations_created": len(recommendations),
            "error": response.get("error")}


def start_agent_workflow(mission_id):
    """Start the existing agent graph for a mission with a new traffic alert."""
    from . import agents

    if not agents.AGENTS_ENABLED:
        return None
    try:
        return agents.run(mission_id)
    except Exception:
        logger.exception("Agent workflow failed for TomTom mission %s", mission_id)
        return None


def validate_recommendation(db: Session, recommendation: models.Recommendation):
    """Fresh TomTom check before approving a live route switch."""
    data = fetch_live_incidents(force_refresh=True)
    if data.get("status") != "live":
        return False, f"Fresh TomTom validation failed ({data.get('error') or data.get('status')})."
    mission = db.query(models.Mission).get(recommendation.mission_id)
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id) if mission else None
    proposed = db.query(models.RouteOption).get(recommendation.recommended_route_id)
    active = db.query(models.RouteOption).get(vehicle.active_route_id) if vehicle and vehicle.active_route_id else None
    if not proposed:
        return False, "The proposed alternative route no longer exists."
    if not mission or not vehicle or not active:
        return False, "The active mission or route no longer exists."
    expected_active_id = (recommendation.extra or {}).get("route_a", {}).get("id")
    if expected_active_id and active.id != expected_active_id:
        return False, "The vehicle's active route changed after this recommendation was created."
    if proposed.id == active.id:
        return False, "The proposed alternative is already the vehicle's active route."

    incidents = data.get("incidents", [])
    proposed_matches = _route_incidents(proposed.polyline, incidents)
    if any(item["confirmed_block"] for item in proposed_matches):
        return False, "A new TomTom road-closure incident blocks the proposed route; the switch was not applied."

    active_matches = _route_incidents(
        _remaining_polyline(active, vehicle.route_progress), incidents
    )
    active_closed = any(item["confirmed_block"] for item in active_matches)
    active_eta = max(0.0, active.base_eta_min * (1 - (vehicle.route_progress or 0)))
    if not active.traffic_aware:
        active_eta += max(
            (_delay_min(item) for item in active_matches if item["incident_type"] == "traffic"),
            default=0.0,
        )
    active_risk = max(
        (0.8 if item["confirmed_block"] else
         0.35 if item["severity"] == "high" else
         0.2 if item["severity"] == "medium" else 0.1 for item in active_matches),
        default=0.0,
    )
    proposed_eta = proposed.base_eta_min
    if not proposed.traffic_aware:
        proposed_eta += max(
            (_delay_min(item) for item in proposed_matches if item["incident_type"] == "traffic"),
            default=0.0,
        )
    proposed_risk = max(
        (0.8 if item["confirmed_block"] else
         0.35 if item["severity"] == "high" else
         0.2 if item["severity"] == "medium" else 0.1 for item in proposed_matches),
        default=0.0,
    )
    saving = (active_eta + active_risk * 6.0) - (proposed_eta + proposed_risk * 6.0)
    if not active_closed and saving < config.TOMTOM_REROUTE_SAVING_MIN:
        return False, (
            "Fresh TomTom and route ETA checks no longer show a meaningful advantage "
            "for the proposed route."
        )

    # Keep the subsequent route rebuild from applying incidents that a successful
    # fresh response has already shown to be cleared.
    checked_at = data.get("checked_at") or time.time()
    external_ids = {item.get("external_id") for item in incidents}
    for record in db.query(models.Incident).filter_by(
        mission_id=mission.id, source="tomtom", status="active"
    ).all():
        record.last_checked_at = checked_at
        if record.external_id not in external_ids:
            record.status = "resolved"
            record.freshness = "resolved"
        else:
            record.freshness = "current"
    active.traffic_status = "affected" if active_matches else "clear"
    return True, (
        f"Fresh TomTom check found no confirmed closure on {proposed.label}; "
        f"estimated route-score improvement is {max(0.0, saving):.1f} min."
    )
