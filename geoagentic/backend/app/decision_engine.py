"""
GeoAgent decision engine.

Rule-based implementation of the seven scenarios from the design doc. Each
`handle_*` function reads the current mission/route/vehicle state, applies a
scenario-specific rule, computes the structured facts (which route, ETA,
risk, confidence, etc.), and returns a Recommendation the dispatcher can review and apply. The
decision logic stays rule-based (deterministic, auditable, offline-safe);
only the natural-language explanation is handed to a real LLM, via
`explain()`.

`explain()` calls the Anthropic Claude API (see config.py) to turn the
already-computed structured facts into a concise, dispatcher-facing sentence.
It is given the facts only and is instructed not to invent new ones, so the
LLM is doing phrasing, not deciding. If ANTHROPIC_API_KEY isn't set or the
call fails, a deterministic plain-English explanation keeps the system
working offline.
"""
import logging
import json
import re
import time
from typing import Optional

import requests
from sqlalchemy.orm import Session

from . import config, models
from .routing import (
    haversine_km,
    point_on_polyline_at_fraction,
    point_to_polyline_distance_km,
    resolve_route_options,
)

logger = logging.getLogger("geoagentic.decision_engine")

RISK_WEIGHT_MIN_PER_UNIT = 6.0  # minutes of "effective delay" added per 1.0 of risk_score


class RouteValidationError(ValueError):
    """A live-traffic recommendation failed its required approval-time check."""

_EXPLAIN_SYSTEM_PROMPT = (
    "You write short, plain-English explanations for an emergency dispatcher. "
    "The JSON facts are the only source of truth. Do not infer or add incidents, "
    "ETAs, causes, route safety, or other details. Keep every provided route name, "
    "vehicle name, and ETA accurate. Use at most five short sentences and no jargon. "
    "Return only the relevant sections, one per line, using exactly these labels: "
    "What happened?; Why does it matter?; What do we recommend?; "
    "Why is this option better?; What should the dispatcher do?"
)


def _llm_explain(facts) -> Optional[str]:
    """Ask Claude to phrase verified facts; invalid/unavailable output falls back."""
    if not config.ANTHROPIC_ENABLED:
        return None

    if not isinstance(facts, dict) or not facts:
        return None

    try:
        resp = requests.post(
            config.ANTHROPIC_API_URL,
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": config.ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": config.CLAUDE_MODEL,
                "max_tokens": config.ANTHROPIC_MAX_TOKENS,
                "system": _EXPLAIN_SYSTEM_PROMPT,
                "messages": [{
                    "role": "user",
                    "content": "Explain these verified facts:\n" +
                    json.dumps(facts, ensure_ascii=True, sort_keys=True),
                }],
            },
            timeout=config.ANTHROPIC_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
        if not text or len(text) > 1400 or "```" in text:
            return None
        allowed_sections = (
            "What happened?", "Why does it matter?", "What do we recommend?",
            "Why is this option better?", "What should the dispatcher do?",
        )
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if any(not any(line.startswith(section) for section in allowed_sections) for line in lines):
            return None
        # Any numeric value in the explanation must have come from the facts.
        allowed_tokens = set()
        for value in _numeric_values(facts):
            if isinstance(value, float):
                allowed_tokens.update((f"{value:g}", f"{value:.1f}", f"{value:.2f}"))
            else:
                allowed_tokens.add(str(value))
        for value in _string_values(facts):
            allowed_tokens.update(re.findall(r"\b\d+(?:\.\d+)?\b", value))
        allowed_tokens = {
            token for value in allowed_tokens
            for token in (value, value.rstrip("0").rstrip("."))
        }
        for number in re.findall(r"\b\d+(?:\.\d+)?\b", text):
            if number not in allowed_tokens:
                return None
        for value in _required_names(facts):
            if value.casefold() not in text.casefold():
                return None
        return text
    except requests.RequestException as exc:
        logger.warning("Claude explain() call failed, falling back to templated reason: %s", exc)
        return None
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("Claude explain() returned an unexpected response, falling back: %s", exc)
        return None


def _numeric_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _numeric_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _numeric_values(child)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield value


def _string_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _string_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _string_values(child)
    elif isinstance(value, str):
        yield value


def _required_names(facts):
    names = []
    vehicle = facts.get("vehicle")
    if vehicle:
        names.append(str(vehicle))
    for route_key in ("current_route", "alternative_route"):
        route = facts.get(route_key)
        if isinstance(route, dict) and route.get("label"):
            names.append(str(route["label"]))
    incident = facts.get("incident")
    if isinstance(incident, dict) and incident.get("source"):
        names.append(str(incident["source"]))
    return names


def _fallback_explanation(facts):
    if not isinstance(facts, dict):
        return " ".join(str(part).strip() for part in facts if part and str(part).strip())
    if "details" in facts:
        return " ".join(str(part).strip() for part in facts["details"] if part and str(part).strip())

    vehicle = facts.get("vehicle")
    incident = facts.get("incident") or {}
    current = facts.get("current_route") or {}
    alternative = facts.get("alternative_route") or {}
    action = facts.get("action", "hold")
    approval = facts.get("approval_status", "waiting")
    source = incident.get("source")
    current_label = current.get("label")
    incident_text = incident.get("description") or incident.get("type")
    sections = []

    if incident_text:
        description = str(incident_text).rstrip(". ")
        happened = f"{source} report: {description}." if source else f"{description}."
        if current_label and incident.get("affects_current_route") is True:
            happened += f" It affects {current_label}."
        elif current_label and incident.get("affects_current_route") is False:
            happened += f" It does not affect {current_label}."
        if incident.get("freshness") == "stale":
            happened += " This report may be out of date."
        sections.append(("What happened?", happened))
    elif facts.get("deviation_m") is not None and current_label:
        sections.append(("What happened?",
                         f"{vehicle or 'The vehicle'} has moved away from planned route {current_label} "
                         f"by {facts['deviation_m']:.0f} m."))

    traffic_unverified = facts.get("traffic_status") in ("unavailable", "stale")
    if traffic_unverified:
        sections.append(("Why does it matter?",
                         "Current traffic information is unavailable or out of date, so route conditions cannot be confirmed."))
    elif incident.get("affects_current_route") is True:
        effect = facts.get("impact")
        if effect:
            sections.append(("Why does it matter?", str(effect)))
        elif current.get("eta_min") is not None:
            sections.append(("Why does it matter?",
                             f"{current_label} is estimated to take {current['eta_min']:.1f} minutes."))

    if traffic_unverified:
        recommendation = "Do not change routes based on unverified traffic information."
    elif action in ("reroute", "recovery_route") and alternative.get("available"):
        recommendation = (
            f"Consider route {alternative.get('label')} for {vehicle or 'this vehicle'} "
            f"to {facts.get('destination') or 'the selected destination'}."
        )
    elif action == "dispatch_backup":
        recommendation = facts.get("recommendation_text", "Consider sending the nearest available backup vehicle.")
    else:
        recommendation = f"Stay on {current_label or 'the current route'} for now."
    if alternative.get("available") is False:
        recommendation = "No alternative route could be verified. Keep the current route under review."
    sections.append(("What do we recommend?", recommendation))

    if alternative.get("blocked"):
        sections.append(("Why is this option better?",
                         f"A reported closure affects route {alternative.get('label')}; it should not be used."))
    elif traffic_unverified:
        sections.append(("Why is this option better?",
                         "No alternative can be verified until fresh traffic information is available."))
    elif alternative.get("available") and current.get("eta_min") is not None and alternative.get("eta_min") is not None:
        comparison = (
            f"Route {alternative['label']} is estimated at {alternative['eta_min']:.1f} minutes, "
            f"compared with {current_label} at {current['eta_min']:.1f} minutes."
        )
        if facts.get("time_saved_min") is not None:
            comparison += f" The estimated saving is {facts['time_saved_min']:.1f} minutes."
        if alternative.get("verification") == "unverified":
            comparison += " Its traffic conditions have not been verified."
        sections.append(("Why is this option better?", comparison))
    elif alternative.get("available") is False and not alternative.get("blocked"):
        sections.append(("Why is this option better?",
                         "There is not enough current information to confirm a better alternative."))

    if approval == "applied":
        next_step = facts.get("approval_message", "The dispatcher approved this change.")
    elif approval == "rejected":
        next_step = "The dispatcher rejected this recommendation. The route was not changed."
    elif approval == "unverified" or traffic_unverified:
        next_step = "Wait for fresh traffic information before approving a route change."
    else:
        next_step = "This recommendation is waiting for dispatcher approval."
    sections.append(("What should the dispatcher do?", next_step))
    return "\n".join(f"{label} {sentence}" for label, sentence in sections)


def explanation_facts(vehicle, incident=None, current_route=None, alternative_route=None,
                      action="hold", time_saved_min=None, impact=None,
                      approval_status="waiting", traffic_status="current",
                      recommendation_text=None, deviation_m=None,
                      current_eta_min=None, alternative_eta_min=None,
                      incident_affects_current_route=None, alternative_available=None):
    """Build the verified, mission-specific facts shared with the LLM and UI."""
    def route_facts(route, available=True, eta_min=None):
        if route is None:
            return None
        return {
            "label": route.label,
            "eta_min": round(
                eta_min if eta_min is not None else route.base_eta_min + (route.delay_min or 0),
                1,
            ),
            "status": route.status,
            "available": available,
            "blocked": route.status == "blocked",
            "traffic_status": route.traffic_status,
        }

    incident_facts = None
    if incident is not None:
        source_value = getattr(incident, "source", "simulation")
        source = {"tomtom": "TomTom", "simulation": "Simulated"}.get(
            source_value, source_value or "Verified"
        )
        incident_route_id = getattr(incident, "affected_route_id", None)
        if incident_affects_current_route is None and incident_route_id:
            incident_affects_current_route = (
                current_route is not None and incident_route_id == current_route.id
            )
        incident_facts = {
            "source": source,
            "type": getattr(incident, "incident_type", "traffic"),
            "description": getattr(incident, "description", None) or
            getattr(incident, "incident_type", "traffic").replace("_", " "),
            "status": getattr(incident, "status", "active"),
            "freshness": getattr(incident, "freshness", "current"),
            "last_checked_at": getattr(incident, "last_checked_at", None),
            "affects_current_route": incident_affects_current_route,
        }
    alt = route_facts(alternative_route, eta_min=alternative_eta_min) if alternative_route else None
    if alt is not None and alternative_route.status == "blocked":
        alt["available"] = False
    if alternative_available is False:
        if alt is None:
            alt = {"available": False}
        else:
            alt["available"] = False
    return {
        "vehicle": vehicle.callsign if vehicle else None,
        "destination": "the selected destination",
        "incident": incident_facts,
        "current_route": route_facts(current_route, eta_min=current_eta_min),
        "alternative_route": alt,
        "time_saved_min": round(time_saved_min, 1) if time_saved_min is not None else None,
        "action": action,
        "impact": impact,
        "approval_status": approval_status,
        "traffic_status": traffic_status,
        "recommendation_text": recommendation_text,
        "deviation_m": deviation_m,
    }


def explain(facts) -> str:
    """Phrase verified facts simply; numerical and entity checks reject drift."""
    if not isinstance(facts, dict):
        facts = {"details": [part.strip() for part in facts if part and part.strip()]}
        return _fallback_explanation(facts)
    llm_text = _llm_explain(facts)
    if llm_text:
        return llm_text
    return _fallback_explanation(facts)


def _update_explanation_status(rec, status, message=None):
    facts = dict((rec.extra or {}).get("explanation_facts") or {})
    if facts:
        facts["approval_status"] = status
        if message:
            facts["approval_message"] = message
        rec.extra = {**(rec.extra or {}), "explanation_facts": facts}
        rec.reason = _fallback_explanation(facts)


def _log(db: Session, mission_id, vehicle_id, event_type, message):
    ev = models.TimelineEvent(mission_id=mission_id, vehicle_id=vehicle_id,
                               event_type=event_type, message=message)
    db.add(ev)
    return ev


def _score(route: models.RouteOption) -> float:
    """Lower is better. Combines ETA + injected delay + a risk penalty."""
    return route.base_eta_min + route.delay_min + route.risk_score * RISK_WEIGHT_MIN_PER_UNIT


def _best_route(routes):
    available = [r for r in routes if r.status != "blocked"]
    pool = available or routes
    return min(pool, key=_score)


def _reroute_from_current_position(db: Session, mission: models.Mission,
                                   vehicle: models.Vehicle, rec: models.Recommendation):
    """Rebuild a reroute at approval time, starting from the vehicle's live GPS."""
    origin = (vehicle.lat, vehicle.lng)
    destination = (mission.dest_lat, mission.dest_lng)
    options, provider = resolve_route_options(
        origin, destination, avg_speed_kmh=vehicle.speed_kmh or vehicle.base_speed_kmh
    )
    incidents = db.query(models.Incident).filter(
        models.Incident.status == "active",
        (models.Incident.mission_id == mission.id) | (models.Incident.mission_id.is_(None)),
    ).all()

    candidates = []
    for option in options:
        route = models.RouteOption(
            mission_id=mission.id,
            label=option["label"],
            polyline=option["polyline"],
            distance_km=option["distance_km"],
            base_eta_min=option["base_eta_min"],
            delay_min=0.0,
            risk_score=0.0,
            status="available",
            is_mock=option.get("is_mock", True),
            traffic_aware=option.get("traffic_aware", False),
        )
        for incident in incidents:
            if incident.lat is None or incident.lng is None:
                continue
            distance_m = point_to_polyline_distance_km(
                (incident.lat, incident.lng), route.polyline
            ) * 1000
            radius_m = max(incident.radius_m or 0, 100)
            if incident.incident_type == "traffic":
                if distance_m <= max(radius_m, 300):
                    route.delay_min = max(
                        route.delay_min,
                        {"low": 4.0, "medium": 9.0, "high": 16.0}.get(incident.severity, 9.0),
                    )
            elif incident.incident_type in ("accident", "roadblock"):
                if distance_m <= radius_m:
                    route.status = "blocked"
            elif incident.incident_type == "flood" and distance_m <= radius_m:
                route.risk_score = min(1.0, route.risk_score + 0.7)
            elif incident.incident_type == "hazard" and distance_m <= radius_m:
                route.risk_score = min(
                    1.0, route.risk_score + (0.9 if vehicle.vehicle_type == "ambulance" else 0.4)
                )
                if vehicle.vehicle_type == "ambulance":
                    route.status = "blocked"
        candidates.append(route)

    safe_candidates = [route for route in candidates if route.status != "blocked"]
    if safe_candidates:
        best = min(safe_candidates, key=_score)
    elif candidates:
        # Do not replace the current route with a route known to intersect a
        # closure. Keep the vehicle moving on its existing route instead.
        current_route = db.query(models.RouteOption).get(vehicle.active_route_id) \
            if vehicle.active_route_id else None
        rec.action = "hold"
        rec.recommended_route_id = current_route.id if current_route else None
        rec.reason = (
            "No clear alternative route was available from the vehicle's current position. "
            "The current route was kept."
        )
        _log(db, mission.id, vehicle.id, "hold", rec.reason)
        return None
    else:
        return None

    existing_routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
    reroute_number = 1 + sum(
        route.label.startswith("R") and "-" in route.label for route in existing_routes
    )
    for route in candidates:
        route.label = f"R{reroute_number}-{route.label}"
        db.add(route)
    db.flush()

    old_route = db.query(models.RouteOption).get(vehicle.active_route_id) \
        if vehicle.active_route_id else None
    if old_route and old_route.id != best.id and old_route.status != "blocked":
        old_route.status = "available"
    best.status = "active"
    vehicle.active_route_id = best.id
    vehicle.route_progress = 0.0
    vehicle.off_route = False
    vehicle.status = models.VehicleStatus.enroute.value
    rec.recommended_route_id = best.id
    rec.reason = (
        f"Replanned from the vehicle's current position using {provider}. "
        f"Best clear route {best.label}: {best.distance_km:.2f} km, "
        f"about {best.base_eta_min + best.delay_min:.1f} min ETA. "
        f"Previous assessment: {rec.reason}"
    )
    rec.extra = {
        **(rec.extra or {}),
        "reroute_origin": {"lat": origin[0], "lng": origin[1]},
        "reroute_provider": provider,
        "reroute_eta_min": round(best.base_eta_min + best.delay_min, 1),
    }
    _log(
        db, mission.id, vehicle.id, "reroute",
        f"Dispatcher applied recommendation: {vehicle.callsign} now following "
        f"{best.label}, recalculated from current position ({best.base_eta_min + best.delay_min:.1f} min ETA).",
    )
    return best


def _mark_routes_near_incident_blocked(db: Session, routes, incident, threshold_km=0.3):
    affected = []
    if incident.lat is None or incident.lng is None:
        return affected
    p = (incident.lat, incident.lng)
    for r in routes:
        d_km = point_to_polyline_distance_km(p, r.polyline)
        if d_km * 1000 <= max(threshold_km * 1000, incident.radius_m):
            r.status = "blocked"
            affected.append(r)
    return affected


def handle_accident(db: Session, mission: models.Mission, incident: models.Incident):
    routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
    active = next((route for route in routes if route.id == vehicle.active_route_id), routes[0])
    blocked = _mark_routes_near_incident_blocked(db, routes, incident)
    best = _best_route(routes)
    _log(db, mission.id, mission.vehicle_id, "incident",
         f"Accident reported near route segment (severity: {incident.severity}).")
    affected_current = active in blocked
    has_alternative = best.id != active.id and best.status != "blocked"
    facts = explanation_facts(
        vehicle, incident, active, best if best.id != active.id else None,
        action="reroute" if has_alternative else "hold",
        current_eta_min=active.base_eta_min + active.delay_min,
        alternative_eta_min=best.base_eta_min + best.delay_min if has_alternative else None,
        time_saved_min=_score(active) - _score(best) if has_alternative else None,
        incident_affects_current_route=affected_current,
        alternative_available=has_alternative if affected_current else None,
        impact=(f"The accident is reported on {active.label}; it may delay the journey."
                if affected_current else None),
    )
    reason = explain(facts)
    confidence = 0.88 if blocked else 0.65
    rec = models.Recommendation(mission_id=mission.id, incident_id=incident.id,
                                 recommended_route_id=best.id if has_alternative else active.id,
                                 action="reroute" if has_alternative else "hold",
                                 reason=reason, confidence=confidence,
                                 extra={"blocked_routes": [r.label for r in blocked],
                                        "explanation_facts": facts})
    db.add(rec)
    return rec


def handle_traffic(db: Session, mission: models.Mission, incident: models.Incident):
    routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
    active = next((r for r in routes if r.status == "active"), routes[0])
    # Apply a congestion multiplier to whichever route the incident sits nearest to.
    target = min(routes, key=lambda r: point_to_polyline_distance_km((incident.lat, incident.lng), r.polyline)) \
        if incident.lat is not None else active
    penalty = {"low": 4.0, "medium": 9.0, "high": 16.0}.get(incident.severity, 9.0)
    target.delay_min = max(target.delay_min, penalty)
    _log(db, mission.id, mission.vehicle_id, "incident",
         f"Severe congestion detected on route {target.label} (+{penalty:.0f} min).")

    best = _best_route(routes)
    improvement = _score(active) - _score(best)
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
    incident_affects_active = target.id == active.id
    has_alternative = (
        best.id != active.id
        and best.status != "blocked"
        and improvement > active.base_eta_min * 0.10
    )
    if has_alternative:
        facts = explanation_facts(
            vehicle, incident, active, best, action="reroute", time_saved_min=improvement,
            current_eta_min=active.base_eta_min + active.delay_min,
            alternative_eta_min=best.base_eta_min + best.delay_min,
            incident_affects_current_route=incident_affects_active,
            impact=(f"Congestion adds about {active.delay_min:.0f} minutes to {active.label}."
                    if incident_affects_active else None),
        )
        reason = explain(facts)
        rec = models.Recommendation(mission_id=mission.id, incident_id=incident.id,
                                     recommended_route_id=best.id if has_alternative else active.id,
                                     action="reroute" if has_alternative else "hold",
                                     reason=reason, confidence=0.75,
                                     extra={"explanation_facts": facts})
    else:
        facts = explanation_facts(
            vehicle, incident, active, action="hold",
            current_eta_min=active.base_eta_min + active.delay_min,
            incident_affects_current_route=incident_affects_active,
            impact=(f"Congestion adds about {active.delay_min:.0f} minutes to {active.label}."
                    if incident_affects_active else None),
            alternative_available=False,
        )
        reason = explain(facts)
        rec = models.Recommendation(mission_id=mission.id, incident_id=incident.id,
                                     recommended_route_id=active.id, action="hold",
                                     reason=reason, confidence=0.6,
                                     extra={"explanation_facts": facts})
    db.add(rec)
    return rec


def handle_roadblock(db: Session, mission: models.Mission, incident: models.Incident):
    routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
    active = next((route for route in routes if route.id == vehicle.active_route_id), routes[0])
    blocked = _mark_routes_near_incident_blocked(db, routes, incident, threshold_km=0.25)
    best = _best_route(routes)
    _log(db, mission.id, mission.vehicle_id, "incident",
         f"Road closure / construction reported (severity: {incident.severity}).")
    has_alternative = best.id != active.id and best.status != "blocked"
    facts = explanation_facts(
        vehicle, incident, active, best if has_alternative else None,
        action="reroute" if has_alternative else "hold",
        current_eta_min=active.base_eta_min + active.delay_min,
        alternative_eta_min=best.base_eta_min + best.delay_min if has_alternative else None,
        time_saved_min=_score(active) - _score(best) if has_alternative else None,
        incident_affects_current_route=active in blocked,
        alternative_available=has_alternative if active in blocked else None,
        impact=f"The roadblock makes {active.label} unavailable." if active in blocked else None,
    )
    reason = explain(facts)
    rec = models.Recommendation(mission_id=mission.id, incident_id=incident.id,
                                 recommended_route_id=best.id if has_alternative else active.id,
                                 action="reroute" if has_alternative else "hold",
                                 reason=reason, confidence=0.82,
                                 extra={"blocked_routes": [r.label for r in blocked],
                                        "explanation_facts": facts})
    db.add(rec)
    return rec


def handle_flood(db: Session, mission: models.Mission, incident: models.Incident):
    routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
    active = next((route for route in routes if route.id == vehicle.active_route_id), routes[0])
    p = (incident.lat, incident.lng)
    affected = []
    for r in routes:
        d_km = point_to_polyline_distance_km(p, r.polyline)
        if d_km * 1000 <= incident.radius_m:
            r.risk_score = min(1.0, r.risk_score + 0.7)
            affected.append(r)
    best = _best_route(routes)
    _log(db, mission.id, mission.vehicle_id, "incident",
         "Flooded / unsafe segment reported.")
    has_alternative = best.id != active.id and best.status != "blocked"
    affects_active = active in affected
    facts = explanation_facts(
        vehicle, incident, active, best if has_alternative else None,
        action="reroute" if has_alternative else "hold",
        current_eta_min=active.base_eta_min + active.delay_min,
        alternative_eta_min=best.base_eta_min + best.delay_min if has_alternative else None,
        time_saved_min=_score(active) - _score(best) if has_alternative else None,
        incident_affects_current_route=affects_active,
        impact=f"Flooding has been reported near {active.label}." if affects_active else None,
        alternative_available=has_alternative if affects_active else None,
    )
    reason = explain(facts)
    rec = models.Recommendation(mission_id=mission.id, incident_id=incident.id,
                                 recommended_route_id=best.id if has_alternative else active.id,
                                 action="reroute" if has_alternative else "hold",
                                 reason=reason, confidence=0.8,
                                 extra={"flagged_routes": [r.label for r in affected],
                                        "explanation_facts": facts})
    db.add(rec)
    return rec


def handle_hazard(db: Session, mission: models.Mission, incident: models.Incident, vehicle: models.Vehicle):
    """Scenario 5: vehicle-specific hazard routing."""
    routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
    p = (incident.lat, incident.lng)
    affected = []
    for r in routes:
        d_km = point_to_polyline_distance_km(p, r.polyline)
        if d_km * 1000 <= incident.radius_m:
            affected.append(r)
            r.risk_score = min(1.0, r.risk_score + (0.9 if vehicle.vehicle_type == "ambulance" else 0.4))
            if vehicle.vehicle_type == "ambulance":
                r.status = "blocked"
    best = _best_route(routes)
    active = next((route for route in routes if route.id == vehicle.active_route_id), routes[0])
    has_alternative = best.id != active.id and best.status != "blocked"
    affects_active = active in affected
    facts = explanation_facts(
        vehicle, incident, active, best if has_alternative else None,
        action="reroute" if has_alternative else "hold",
        current_eta_min=active.base_eta_min + active.delay_min,
        alternative_eta_min=best.base_eta_min + best.delay_min if has_alternative else None,
        time_saved_min=_score(active) - _score(best) if has_alternative else None,
        incident_affects_current_route=affects_active,
        impact=f"The hazard affects {active.label}." if affects_active else None,
        alternative_available=has_alternative if affects_active else None,
    )
    reason = explain(facts)
    _log(db, mission.id, vehicle.id, "incident",
         f"Hazard zone active — re-evaluated route for {vehicle.callsign}.")
    rec = models.Recommendation(mission_id=mission.id, incident_id=incident.id,
                                 recommended_route_id=best.id if has_alternative else active.id,
                                 action="reroute" if has_alternative else "hold",
                                 reason=reason, confidence=0.78,
                                 extra={"explanation_facts": facts})
    db.add(rec)
    return rec


def handle_route_deviation(db: Session, mission: models.Mission, vehicle: models.Vehicle,
                            corridor_threshold_m: float = 250.0):
    routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
    active = next((r for r in routes if r.id == vehicle.active_route_id), routes[0])
    d_km = point_to_polyline_distance_km((vehicle.lat, vehicle.lng), active.polyline)
    vehicle.deviation_m = round(d_km * 1000, 1)

    if vehicle.deviation_m <= corridor_threshold_m:
        vehicle.off_route = False
        return None

    vehicle.off_route = True
    vehicle.status = models.VehicleStatus.off_route.value
    _log(db, mission.id, vehicle.id, "deviation",
         f"{vehicle.callsign} is {vehicle.deviation_m:.0f} m off planned route {active.label}. Status: OFF_ROUTE.")

    from .routing import generate_route_options
    dest = (mission.dest_lat, mission.dest_lng)
    cur = (vehicle.lat, vehicle.lng)
    recovery = generate_route_options(cur, dest, avg_speed_kmh=vehicle.speed_kmh)[0]
    r = models.RouteOption(mission_id=mission.id, label="RECOVERY",
                            polyline=recovery["polyline"], distance_km=recovery["distance_km"],
                            base_eta_min=recovery["base_eta_min"], status="available",
                            is_mock=recovery.get("is_mock", True))
    db.add(r)
    db.flush()

    facts = explanation_facts(
        vehicle, current_route=active, alternative_route=r,
        action="recovery_route", deviation_m=vehicle.deviation_m,
        alternative_eta_min=r.base_eta_min,
        impact=f"The vehicle is {vehicle.deviation_m:.0f} m away from the planned route.",
    )
    reason = explain(facts)
    rec = models.Recommendation(mission_id=mission.id, incident_id=None,
                                 recommended_route_id=r.id, action="recovery_route",
                                 reason=reason, confidence=0.85,
                                 extra={"explanation_facts": facts})
    db.add(rec)
    return rec


def handle_mass_casualty(db: Session, mission: models.Mission, incident: models.Incident, db_session: Session):
    """Scenario 7: resource / backup-vehicle recommendation."""
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
    # The primary unit is on scene for this scenario and must not keep
    # advancing along its original destination route.
    if incident.lat is not None and incident.lng is not None:
        vehicle.lat, vehicle.lng = incident.lat, incident.lng
    vehicle.status = models.VehicleStatus.at_incident.value
    vehicle.speed_kmh = 0.0
    _log(db, mission.id, vehicle.id, "arrival",
         f"{vehicle.callsign} stopped at the mass-casualty scene and is awaiting backup.")

    candidates = db.query(models.Vehicle).filter(
        models.Vehicle.vehicle_type == "ambulance",
        models.Vehicle.id != vehicle.id,
        models.Vehicle.status.in_([models.VehicleStatus.idle.value]),
    ).all()

    target = (incident.lat, incident.lng) if incident.lat is not None else (mission.dest_lat, mission.dest_lng)
    scored = []
    for c in candidates:
        options, _ = resolve_route_options(
            (c.lat, c.lng), target, avg_speed_kmh=c.base_speed_kmh or c.speed_kmh or 45.0
        )
        best_option = min(options, key=lambda option: option["base_eta_min"])
        dist_km = best_option["distance_km"]
        eta_min = best_option["base_eta_min"]
        scored.append((c, dist_km, eta_min))
    scored.sort(key=lambda x: x[2])

    _log(db, mission.id, vehicle.id, "incident",
         f"Mass-casualty incident declared (severity: {incident.severity}). Evaluating backup capacity.")

    if not scored:
        facts = explanation_facts(
            vehicle, incident,
            current_route=db.query(models.RouteOption).get(vehicle.active_route_id)
            if vehicle.active_route_id else None,
            action="hold",
            recommendation_text="No backup ambulance is available. Request help from another service.",
        )
        reason = explain(facts)
        rec = models.Recommendation(mission_id=mission.id, incident_id=incident.id,
                                     recommended_route_id=None, action="hold",
                                     reason=reason, confidence=0.5,
                                     extra={"candidates": [], "explanation_facts": facts})
        db.add(rec)
        return rec

    best_v, dist_km, eta_min = scored[0]
    facts = explanation_facts(
        vehicle, incident,
        current_route=db.query(models.RouteOption).get(vehicle.active_route_id)
        if vehicle.active_route_id else None,
        action="dispatch_backup",
        recommendation_text=(
            f"Send {best_v.callsign} to the scene. Its estimated arrival time is {eta_min:.1f} minutes."
        ),
        impact=f"{vehicle.callsign} is at the scene and additional help may be needed.",
    )
    facts["backup_vehicle"] = best_v.callsign
    facts["backup_eta_min"] = round(eta_min, 1)
    facts["backup_distance_km"] = round(dist_km, 2)
    reason = explain(facts)
    rec = models.Recommendation(
        mission_id=mission.id, incident_id=incident.id, recommended_route_id=None,
        action="dispatch_backup", reason=reason, confidence=0.8,
        extra={"backup_vehicle_id": best_v.id, "backup_callsign": best_v.callsign,
               "eta_min": round(eta_min, 1),
               "incident_lat": target[0], "incident_lng": target[1],
               "candidates": [{"callsign": c.callsign, "eta_min": round(e, 1)} for c, _, e in scored],
               "explanation_facts": facts},
    )
    db.add(rec)
    return rec


# ---------------------------------------------------------------------------
# Applying / rejecting a recommendation (shared by the REST endpoints and the
# LangGraph "act" node so both paths behave identically)
# ---------------------------------------------------------------------------
def apply_recommendation(db: Session, rec: models.Recommendation) -> models.Recommendation:
    if rec.status != "pending":
        return rec

    mission = db.query(models.Mission).get(rec.mission_id)
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)

    if rec.action in ("reroute", "recovery_route") and rec.recommended_route_id:
        if (rec.extra or {}).get("monitor_source") == "tomtom":
            from .traffic_monitor import validate_recommendation
            valid, validation_message = validate_recommendation(db, rec)
            if not valid:
                _update_explanation_status(rec, "unverified", validation_message)
                db.commit()
                raise RouteValidationError(validation_message)
            rec.extra = {
                **rec.extra,
                "approval_validation": {
                    "status": "live_clear",
                    "checked_at": time.time(),
                    "message": validation_message,
                },
            }
        _reroute_from_current_position(db, mission, vehicle, rec)
    elif rec.action == "dispatch_backup" and rec.extra:
        backup = db.query(models.Vehicle).get(rec.extra.get("backup_vehicle_id"))
        incident = db.query(models.Incident).get(rec.incident_id) if rec.incident_id else None
        target = None
        if incident and incident.lat is not None and incident.lng is not None:
            target = (incident.lat, incident.lng)
        elif rec.extra.get("incident_lat") is not None and rec.extra.get("incident_lng") is not None:
            target = (rec.extra["incident_lat"], rec.extra["incident_lng"])

        if backup and backup.status == models.VehicleStatus.idle.value and target:
            options, provider = resolve_route_options(
                (backup.lat, backup.lng), target,
                avg_speed_kmh=backup.base_speed_kmh or backup.speed_kmh or 45.0,
            )
            best_option = min(options, key=lambda option: option["base_eta_min"])
            backup_mission = models.Mission(
                vehicle_id=backup.id,
                mission_type="mass_casualty_backup",
                origin_lat=backup.lat,
                origin_lng=backup.lng,
                dest_lat=target[0],
                dest_lng=target[1],
            )
            db.add(backup_mission)
            db.flush()
            new_routes = []
            for option in options:
                route = models.RouteOption(
                    mission_id=backup_mission.id,
                    label=option["label"],
                    polyline=option["polyline"],
                    distance_km=option["distance_km"],
                    base_eta_min=option["base_eta_min"],
                    delay_min=0.0,
                    risk_score=0.0,
                    status="active" if option is best_option else "available",
                    is_mock=option.get("is_mock", True),
                    traffic_aware=option.get("traffic_aware", False),
                )
                db.add(route)
                new_routes.append(route)
            db.flush()
            active_route = next(route for route in new_routes if route.status == "active")
            backup.current_mission_id = backup_mission.id
            backup.active_route_id = active_route.id
            backup.route_progress = 0.0
            backup.off_route = False
            backup.status = models.VehicleStatus.enroute.value
            backup.speed_kmh = backup.base_speed_kmh or 45.0

            actual_eta = best_option["base_eta_min"]
            rec.extra = {**rec.extra, "eta_min": round(actual_eta, 1),
                         "backup_mission_id": backup_mission.id,
                         "backup_route_provider": provider}
            rec.reason = (
                f"{backup.callsign} dispatched from its current position to the mass-casualty "
                f"scene via route {best_option['label']}. Estimated arrival: {actual_eta:.1f} min."
            )
            _log(db, backup_mission.id, backup.id, "dispatch",
                 f"Backup unit {backup.callsign} dispatched to the mass-casualty scene. "
                 f"Route {best_option['label']}, ETA {actual_eta:.1f} min.")
        else:
            reason = (
                "Backup dispatch could not be completed: the selected ambulance is no longer "
                "available or the scene location is missing."
            )
            rec.reason = reason
            rec.action = "hold"
            _log(db, mission.id, vehicle.id, "hold", reason)
    elif rec.action == "hold":
        _log(db, mission.id, vehicle.id, "hold", "Dispatcher confirmed: holding current route.")

    rec.status = "applied"
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
    active_route = db.query(models.RouteOption).get(vehicle.active_route_id) if vehicle.active_route_id else None
    approved_message = "The dispatcher approved this recommendation."
    if rec.action in ("reroute", "recovery_route") and active_route:
        approved_message = (
            f"The dispatcher approved the change. {vehicle.callsign} is now on route {active_route.label}."
        )
    _update_explanation_status(rec, "applied", approved_message)
    db.commit()
    db.refresh(rec)
    return rec


def reject_recommendation(db: Session, rec: models.Recommendation) -> models.Recommendation:
    rec.status = "rejected"
    _update_explanation_status(rec, "rejected")
    mission = db.query(models.Mission).get(rec.mission_id)
    vehicle = db.query(models.Vehicle).get(mission.vehicle_id) if mission else None
    _log(db, rec.mission_id, vehicle.id if vehicle else None, "recommendation",
         f"Dispatcher rejected recommendation for {vehicle.callsign}." if vehicle else
         "Dispatcher rejected recommendation.")
    db.commit()
    db.refresh(rec)
    return rec
