"""
GeoAgentic multi-agent orchestration, built on LangGraph.

    START -> observe -> supervisor <-> {trajectory, incident, eta_impact,
                                        route, resource}
                            |
                            v
                        recommend -> human_approval (interrupt) -> act -> END
                                     (act applies on approve, rejects otherwise)

* Shared "world state" is the typed graph state (`AgentState`).
* The **supervisor** decides, from what the specialists have found so far,
  which agent runs next (and skips agents that aren't needed).
* Specialist agents are deterministic: they read the DB, apply the same
  scoring rules as decision_engine.py, and report facts. The LLM is used
  only by `decision_engine.explain()` to phrase the final explanation.
* **Human in the loop**: `human_approval` calls `interrupt()`, which pauses
  the graph (checkpointed in memory) until the operator approves or rejects.
  Nothing is applied to a vehicle without approval.

Approval can arrive through `resume()` (POST /api/agents/runs/{id}/resume)
or through the existing dashboard Apply/Dismiss endpoints, which call
`resume_for_recommendation()` so both paths drive the same graph.

If `langgraph` isn't installed, `AGENTS_ENABLED` is False and the REST
endpoints return 503; the rest of the app is unaffected.
"""
import logging
import operator
import uuid
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from . import config, decision_engine, models
from .database import SessionLocal
from .routing import (
    haversine_km, point_to_polyline_distance_km, resolve_route_options,
)

logger = logging.getLogger("geoagentic.agents")
_SEVERITY_DELAY_MIN = {"low": 4.0, "medium": 9.0, "high": 16.0}

AGENTS_ENABLED = config.AGENTS_ENABLED

if AGENTS_ENABLED:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    mission_id: str
    world: Dict[str, Any]           # snapshot loaded by `observe`
    findings: Dict[str, Any]        # per-agent outputs, keyed by agent name
    completed: List[str]            # agents already run/skipped
    next: str                       # supervisor's routing decision
    trace: Annotated[List[Dict[str, str]], operator.add]   # append-only log
    recommendation: Optional[Dict[str, Any]]
    decision: Optional[str]         # "approve" | "reject"
    outcome: str


def _t(agent: str, message: str) -> List[Dict[str, str]]:
    return [{"agent": agent, "message": message}]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def observe(state: AgentState):
    """Load the current mission picture from the DB into the graph state."""
    db = SessionLocal()
    try:
        mission = db.query(models.Mission).get(state["mission_id"])
        if not mission:
            raise ValueError(f"mission {state['mission_id']} not found")
        vehicle = db.query(models.Vehicle).get(mission.vehicle_id)
        routes = db.query(models.RouteOption).filter_by(mission_id=mission.id).all()
        incidents = db.query(models.Incident).filter_by(status="active").all()
        incidents = [i for i in incidents
                     if i.mission_id == mission.id
                     or (i.incident_type == "hazard" and not i.mission_id)]
        incidents = [i for i in incidents
                     if i.source != "tomtom" or i.freshness == "current"]
        world = {
            "vehicle": {
                "id": vehicle.id, "callsign": vehicle.callsign,
                "type": vehicle.vehicle_type, "lat": vehicle.lat, "lng": vehicle.lng,
                "speed_kmh": vehicle.speed_kmh, "active_route_id": vehicle.active_route_id,
            },
            "mission": {
                "id": mission.id, "dest": [mission.dest_lat, mission.dest_lng],
            },
            "routes": [{
                "id": r.id, "label": r.label, "polyline": r.polyline,
                "distance_km": r.distance_km, "base_eta_min": r.base_eta_min,
                "delay_min": r.delay_min, "risk_score": r.risk_score,
                "status": r.status, "is_mock": r.is_mock,
                "traffic_aware": r.traffic_aware, "traffic_status": r.traffic_status,
            } for r in routes],
            "incidents": [{
                "id": i.id, "type": i.incident_type, "lat": i.lat, "lng": i.lng,
                "radius_m": i.radius_m, "severity": i.severity, "source": i.source,
                "external_id": i.external_id, "geometry": i.geometry,
                "description": i.description, "delay_s": i.delay_s,
                "icon_category": i.icon_category, "source_data": i.source_data,
            } for i in incidents],
        }
        return {
            "world": world, "findings": {}, "completed": [], "recommendation": None,
            "trace": _t("observe", f"{vehicle.callsign}: {len(routes)} route(s), "
                                    f"{len(incidents)} active incident(s)."),
        }
    finally:
        db.close()


def _active_route(world):
    aid = world["vehicle"]["active_route_id"]
    return next((r for r in world["routes"] if r["id"] == aid), world["routes"][0])


def supervisor(state: AgentState):
    """Pick the next specialist from what is known so far, skipping agents
    whose input isn't needed. Ends in `recommend` (something to propose) or
    END (nothing to do)."""
    done = list(state.get("completed", []))
    world, f = state["world"], state.get("findings", {})

    def go(agent, why):
        return {"next": agent, "trace": _t("supervisor", f"-> {agent}: {why}")}

    if "trajectory" not in done:
        return go("trajectory", "check where the vehicle is vs its planned route")
    if "incident" not in done:
        if world["incidents"]:
            return go("incident", f"{len(world['incidents'])} active incident(s) to assess")
        f["incident"] = {"affected": []}
        done.append("incident")
        return {"next": "supervisor", "completed": done, "findings": f,
                "trace": _t("supervisor", "no active incidents; skipping incident agent")}
    if "eta_impact" not in done:
        return go("eta_impact", "quantify delay per route")

    off = f["trajectory"]["off_route"]
    affected = f["incident"]["affected"]
    delay = f["eta_impact"]["active_delay_min"]
    has_mci = any(i["type"] == "mass_casualty" for i in world["incidents"])

    if (off or affected or delay > 0) and "route" not in done:
        return go("route", "evaluate alternatives / recovery")
    backup_needed = has_mci or (
        world["vehicle"]["type"] == "ambulance"
        and delay >= config.AGENT_BACKUP_DELAY_MIN
        and f.get("route", {}).get("action") != "reroute")
    if backup_needed and "resource" not in done:
        return go("resource", "check nearby backup capacity")

    if not (off or affected or delay > 0 or has_mci):
        return {"next": "end", "outcome": "no_action",
                "trace": _t("supervisor", "mission nominal; no recommendation needed")}
    return {"next": "recommend",
            "trace": _t("supervisor", "specialists done; composing recommendation")}


def _finish(agent, state, result, message):
    findings = dict(state.get("findings", {}))
    findings[agent] = result
    return {"findings": findings,
            "completed": list(state.get("completed", [])) + [agent],
            "trace": _t(agent, message)}


def trajectory_agent(state: AgentState):
    w = state["world"]
    active = _active_route(w)
    d_m = point_to_polyline_distance_km(
        (w["vehicle"]["lat"], w["vehicle"]["lng"]), active["polyline"]) * 1000
    off = d_m > config.AGENT_DEVIATION_THRESHOLD_M
    return _finish("trajectory", state, {"deviation_m": round(d_m, 1), "off_route": off},
                   f"{w['vehicle']['callsign']} is {d_m:.0f} m from route {active['label']}"
                   + (" -> OFF ROUTE" if off else " (on route)"))


def incident_agent(state: AgentState):
    """Which incidents touch which routes, and how (block / delay / risk)."""
    w = state["world"]
    is_amb = w["vehicle"]["type"] == "ambulance"
    affected = []
    for inc in w["incidents"]:
        if inc["lat"] is None:
            continue
        if inc.get("source") == "tomtom":
            from .traffic_monitor import geometry_distance_m
            for r in w["routes"]:
                distance_m = geometry_distance_m(r["polyline"], inc.get("geometry"),
                                                 (inc["lat"], inc["lng"]))
                if distance_m is None or distance_m > config.TOMTOM_ROUTE_PROXIMITY_M:
                    continue
                if (inc.get("icon_category") == 8
                        and distance_m <= config.TOMTOM_CLOSURE_MATCH_M
                        and inc.get("geometry", {}).get("type") in ("LineString", "MultiLineString")):
                    effect, details = "blocked", {}
                elif inc["type"] == "traffic" and not r.get("traffic_aware"):
                    effect = "delay"
                    details = {"delay_min": min(
                        120.0, inc["delay_s"] / 60
                    ) if isinstance(inc.get("delay_s"), (int, float)) and inc["delay_s"] > 0
                        else _SEVERITY_DELAY_MIN.get(inc["severity"], 9.0)}
                else:
                    effect, details = "risk", {"risk": 0.35 if inc["severity"] == "high" else 0.2}
                affected.append({
                    "incident": inc["id"], "source": "TomTom", "type": inc["type"],
                    "route": r["label"], "distance_m": round(distance_m),
                    "effect": effect, **details,
                })
            continue
        p = (inc["lat"], inc["lng"])
        ranked = sorted(w["routes"], key=lambda r: point_to_polyline_distance_km(p, r["polyline"]))
        for r in w["routes"]:
            d_m = point_to_polyline_distance_km(p, r["polyline"]) * 1000
            t = inc["type"]
            if t in ("accident", "roadblock"):
                thr = max(300.0 if t == "accident" else 250.0, inc["radius_m"])
                if d_m <= thr:
                    affected.append({"incident": inc["id"], "type": t, "route": r["label"],
                                     "distance_m": round(d_m), "effect": "blocked"})
            elif t == "flood" and d_m <= inc["radius_m"]:
                affected.append({"incident": inc["id"], "type": t, "route": r["label"],
                                 "distance_m": round(d_m), "effect": "risk", "risk": 0.7})
            elif t == "hazard" and d_m <= inc["radius_m"]:
                affected.append({"incident": inc["id"], "type": t, "route": r["label"],
                                 "distance_m": round(d_m),
                                 "effect": "blocked" if is_amb else "risk",
                                 "risk": 0.9 if is_amb else 0.4})
            elif t == "traffic" and r is ranked[0]:
                penalty = {"low": 4.0, "medium": 9.0, "high": 16.0}.get(inc["severity"], 9.0)
                affected.append({"incident": inc["id"], "type": t, "route": r["label"],
                                 "distance_m": round(d_m), "effect": "delay", "delay_min": penalty})
    return _finish("incident", state, {"affected": affected},
                   f"{len(affected)} route/incident interaction(s): "
                   + ", ".join(sorted({f"{a['type']}->{a['route']} ({a['effect']})" for a in affected}))
                   if affected else "no incident touches any route")


def _effective(world, affected):
    """Per-route effective state = DB state combined with incident effects.
    Uses max() so it is idempotent if the rule engine already applied them."""
    out = {}
    for r in world["routes"]:
        e = dict(r)
        for a in affected:
            if a["route"] != r["label"]:
                continue
            if a["effect"] == "blocked":
                e["status"] = "blocked"
            if a["effect"] == "risk" or "risk" in a:
                e["risk_score"] = min(1.0, max(e["risk_score"], a.get("risk", 0)))
            if a["effect"] == "delay":
                e["delay_min"] = max(e["delay_min"], a["delay_min"])
        e["eta_min"] = e["base_eta_min"] + e["delay_min"]
        e["score"] = e["eta_min"] + e["risk_score"] * decision_engine.RISK_WEIGHT_MIN_PER_UNIT
        out[r["label"]] = e
    return out


def eta_impact_agent(state: AgentState):
    w, f = state["world"], state["findings"]
    eff = _effective(w, f["incident"]["affected"])
    active = _active_route(w)
    a = eff[active["label"]]
    result = {
        "original_eta_min": round(active["base_eta_min"], 1),
        "current_eta_min": round(a["eta_min"], 1),
        "active_delay_min": round(a["delay_min"], 1),
        "active_blocked": a["status"] == "blocked",
        "routes": {k: {"eta_min": round(v["eta_min"], 1), "risk": round(v["risk_score"], 2),
                        "blocked": v["status"] == "blocked", "score": round(v["score"], 1)}
                   for k, v in eff.items()},
    }
    return _finish("eta_impact", state, result,
                   f"route {active['label']}: ETA {result['original_eta_min']} -> "
                   f"{result['current_eta_min']} min (+{result['active_delay_min']})"
                   + (", BLOCKED" if result["active_blocked"] else ""))


def route_agent(state: AgentState):
    w, f = state["world"], state["findings"]
    eff = _effective(w, f["incident"]["affected"])
    active = _active_route(w)
    pool = [v for v in eff.values() if v["status"] != "blocked" and v["label"] != "RECOVERY"] \
        or list(eff.values())
    best = min(pool, key=lambda v: v["score"])
    improvement = eff[active["label"]]["score"] - best["score"]
    result: Dict[str, Any] = {"best": best["label"], "improvement_min": round(improvement, 1),
                              "action": "hold", "recovery": None}
    if f["trajectory"]["off_route"]:
        v = w["vehicle"]
        routes, provider = resolve_route_options((v["lat"], v["lng"]), tuple(w["mission"]["dest"]),
                                                  avg_speed_kmh=v["speed_kmh"])
        result.update(action="recovery_route", recovery={**routes[0], "provider": provider})
        msg = (f"off route: recovery route via {provider}: "
               f"{routes[0]['distance_km']:.2f} km, {routes[0]['base_eta_min']:.1f} min")
    elif best["label"] != active["label"] and (
            eff[active["label"]]["status"] == "blocked"
            or improvement > active["base_eta_min"] * 0.10):
        result["action"] = "reroute"
        msg = f"reroute {active['label']} -> {best['label']} saves {improvement:.1f} min"
    else:
        msg = f"keep route {active['label']}; no alternative is meaningfully better"
    return _finish("route", state, result, msg)


def resource_agent(state: AgentState):
    w = state["world"]
    db = SessionLocal()
    try:
        inc = next((i for i in w["incidents"] if i["type"] == "mass_casualty"), None)
        target = (inc["lat"], inc["lng"]) if inc and inc["lat"] is not None else tuple(w["mission"]["dest"])
        vt = "ambulance" if w["vehicle"]["type"] == "ambulance" else w["vehicle"]["type"]
        cands = db.query(models.Vehicle).filter(
            models.Vehicle.vehicle_type == vt,
            models.Vehicle.id != w["vehicle"]["id"],
            models.Vehicle.status == models.VehicleStatus.idle.value).all()
        scored = []
        for candidate in cands:
            options, _ = resolve_route_options(
                (candidate.lat, candidate.lng), target,
                avg_speed_kmh=candidate.base_speed_kmh or candidate.speed_kmh or 45.0,
            )
            best_route = min(options, key=lambda option: option["base_eta_min"])
            scored.append((candidate.id, candidate.callsign, best_route["distance_km"],
                           best_route["base_eta_min"]))
        scored.sort(key=lambda candidate: candidate[3])
    finally:
        db.close()
    result = {"candidates": [{"id": i, "callsign": cs, "dist_km": round(d, 2), "eta_min": round(e, 1)}
                             for i, cs, d, e in scored]}
    msg = (f"nearest idle backup {scored[0][1]}: {scored[0][3]:.1f} min away"
           if scored else "no idle backup unit available (consider mutual aid)")
    return _finish("resource", state, result, msg)


def recommend(state: AgentState):
    """Compose the recommendation, persist it as 'pending' and pass it to
    the human. Runs before the interrupt so its DB writes happen once."""
    w, f = state["world"], state["findings"]
    route, res = f.get("route", {}), f.get("resource", {})
    eta = f["eta_impact"]
    active = _active_route(w)
    cands = res.get("candidates", [])

    db = SessionLocal()
    try:
        slim_route = {k: v for k, v in route.items() if k != "recovery"}
        route_id, extra = None, {"agent_team": True,
                                 "findings": {**f, "route": slim_route}}
        tomtom_incident = next(
            (incident for incident in w["incidents"] if incident.get("source") == "tomtom"),
            None,
        )
        if tomtom_incident:
            extra.update(
                monitor_source="tomtom",
                route_a={"id": active["id"]},
                incident_fingerprint=(tomtom_incident.get("source_data") or {}).get("fingerprint", ""),
            )
        if route.get("action") == "recovery_route":
            action, confidence = "recovery_route", 0.85
            rc = route["recovery"]
            r = models.RouteOption(mission_id=w["mission"]["id"], label="RECOVERY",
                                   polyline=rc["polyline"], distance_km=rc["distance_km"],
                                   base_eta_min=rc["base_eta_min"], status="available",
                                   is_mock=rc.get("is_mock", True))
            db.add(r); db.flush(); route_id = r.id
            parts = [f"{w['vehicle']['callsign']} deviated {f['trajectory']['deviation_m']:.0f} m from route {active['label']}.",
                     f"Recovery route ({rc.get('provider', 'unknown')}): {rc['distance_km']:.2f} km, {rc['base_eta_min']:.1f} min."]
        elif route.get("action") == "reroute":
            action, confidence = "reroute", 0.88 if eta["active_blocked"] else 0.75
            route_id = next(r["id"] for r in w["routes"] if r["label"] == route["best"])
            parts = [f"Route {active['label']} is " + ("blocked" if eta["active_blocked"]
                     else f"delayed +{eta['active_delay_min']:.0f} min") + " due to active incidents.",
                     f"Route {route['best']} saves {route['improvement_min']:.1f} min "
                     f"(ETA {eta['routes'][route['best']]['eta_min']} min)."]
        elif cands and (any(i["type"] == "mass_casualty" for i in w["incidents"])
                        or eta["active_delay_min"] >= config.AGENT_BACKUP_DELAY_MIN):
            b = cands[0]
            action, confidence = "dispatch_backup", 0.8
            extra.update(backup_vehicle_id=b["id"], backup_callsign=b["callsign"],
                         eta_min=b["eta_min"],
                         incident_lat=next((i["lat"] for i in w["incidents"]
                                            if i["type"] == "mass_casualty"), None),
                         incident_lng=next((i["lng"] for i in w["incidents"]
                                            if i["type"] == "mass_casualty"), None),
                         candidates=[{"callsign": c["callsign"], "eta_min": c["eta_min"]} for c in cands])
            parts = [f"{w['vehicle']['callsign']} alone may be insufficient or badly delayed.",
                     f"Dispatch {b['callsign']}: about {b['eta_min']:.1f} min away."]
        else:
            action, confidence = "hold", 0.6
            parts = [f"Route {active['label']} delay is {eta['active_delay_min']:.0f} min,",
                     "but no alternative offers a meaningful improvement. Holding current route."]

        incident = next(iter(w["incidents"]), None)
        current_eta = eta["routes"].get(active["label"], {}).get("eta_min", active["base_eta_min"])
        alternative = next((item for item in w["routes"] if item["id"] == route_id), None)
        explanation_data = {
            "vehicle": w["vehicle"]["callsign"],
            "incident": ({
                "source": "TomTom" if incident.get("source") == "tomtom" else
                "Simulated" if incident.get("source") == "simulation" else "Verified",
                "type": incident.get("type", "incident").replace("_", " "),
                "description": incident.get("description") or
                incident.get("type", "incident").replace("_", " "),
                "status": "active",
                "freshness": incident.get("freshness", "current"),
                "affects_current_route": any(
                    item.get("route") == active["label"]
                    for item in f.get("incident", {}).get("affected", [])
                ),
            } if incident else None),
            "current_route": {
                "label": active["label"], "eta_min": current_eta,
                "status": ("blocked" if eta["routes"].get(active["label"], {}).get("blocked")
                           else active["status"]),
            },
            "alternative_route": ({
                "label": alternative["label"],
                "eta_min": eta["routes"].get(alternative["label"], {}).get(
                    "eta_min", alternative["base_eta_min"]
                ),
                "status": ("blocked" if eta["routes"].get(alternative["label"], {}).get("blocked")
                           else alternative["status"]),
                "available": not eta["routes"].get(alternative["label"], {}).get("blocked", False),
                "blocked": eta["routes"].get(alternative["label"], {}).get("blocked", False),
            } if alternative and alternative["id"] != active["id"] else None),
            "time_saved_min": (
                route.get("improvement_min")
                if action == "reroute" else None
            ),
            "action": action,
            "impact": (
                f"The vehicle is {f['trajectory']['deviation_m']:.0f} m from its planned route."
                if f["trajectory"]["off_route"] else
                f"The current route is estimated to take {current_eta:.1f} minutes."
                if f["incident"].get("affected") or eta["active_delay_min"] > 0 else None
            ),
            "approval_status": "waiting",
            "traffic_status": "current",
            "recommendation_text": (
                f"Send {cands[0]['callsign']} to the scene. Its estimated arrival time is "
                f"{cands[0]['eta_min']:.1f} minutes."
                if action == "dispatch_backup" and cands else None
            ),
        }
        if action == "dispatch_backup" and cands:
            explanation_data["backup_vehicle"] = cands[0]["callsign"]
            explanation_data["backup_eta_min"] = cands[0]["eta_min"]
        reason = decision_engine.explain(explanation_data)
        inc_id = tomtom_incident["id"] if tomtom_incident else (
            w["incidents"][0]["id"] if w["incidents"] else None
        )
        existing = None
        if tomtom_incident:
            existing = next((
                candidate for candidate in db.query(models.Recommendation).filter_by(
                    mission_id=w["mission"]["id"], status="pending"
                ).all()
                if (candidate.extra or {}).get("monitor_source") == "tomtom"
            ), None)
        if existing:
            rec = existing
            explanation_data["action"] = rec.action
            explanation_data["alternative_route"] = None
            explanation_data["approval_status"] = "waiting"
            rec.extra = {
                **(rec.extra or {}),
                "agent_team": True,
                "findings": {**f, "route": slim_route},
                "agent_trace": list(state.get("trace", [])),
                "explanation_facts": explanation_data,
            }
            action, confidence, route_id = rec.action, rec.confidence, rec.recommended_route_id
            rec.reason = reason = decision_engine.explain(explanation_data)
        else:
            rec = models.Recommendation(
                mission_id=w["mission"]["id"], incident_id=inc_id,
                recommended_route_id=route_id, action=action, reason=reason,
                confidence=confidence, extra={**extra, "explanation_facts": explanation_data},
            )
            db.add(rec)
        decision_engine._log(db, w["mission"]["id"], w["vehicle"]["id"], "agent",
                             f"GeoAgent team recommends {action.upper()} (awaiting operator approval).")
        db.commit(); db.refresh(rec)
        payload = {"id": rec.id, "action": action, "reason": reason,
                   "confidence": confidence, "recommended_route_id": route_id, "status": "pending"}
    finally:
        db.close()
    return {"recommendation": payload,
            "trace": _t("recommend", f"{action} proposed (confidence {confidence:.0%})")}


def human_approval(state: AgentState):
    """Pause until the operator decides. Nothing runs before the interrupt
    that must not be repeated (the node body re-executes on resume)."""
    rec = state["recommendation"]
    answer = interrupt({"recommendation_id": rec["id"], "action": rec["action"],
                        "question": "Approve this recommendation?"})
    decision = "approve" if str(answer).lower() in ("approve", "approved", "apply") else "reject"
    return {"decision": decision, "trace": _t("operator", f"decision: {decision}")}


def act(state: AgentState):
    db = SessionLocal()
    try:
        rec = db.query(models.Recommendation).get(state["recommendation"]["id"])
        if state["decision"] == "approve":
            decision_engine.apply_recommendation(db, rec)
            outcome, msg = "applied", f"{rec.action} applied"
        else:
            decision_engine.reject_recommendation(db, rec)
            outcome, msg = "rejected", "recommendation rejected; no change made"
    finally:
        db.close()
    _PENDING.pop(state["recommendation"]["id"], None)
    return {"outcome": outcome, "trace": _t("act", msg)}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
_PENDING: Dict[str, str] = {}   # recommendation_id -> thread_id awaiting approval
_graph = None


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [("observe", observe), ("supervisor", supervisor),
                     ("trajectory", trajectory_agent), ("incident", incident_agent),
                     ("eta_impact", eta_impact_agent), ("route", route_agent),
                     ("resource", resource_agent), ("recommend", recommend),
                     ("human_approval", human_approval), ("act", act)]:
        g.add_node(name, fn)
    g.add_edge(START, "observe")
    g.add_edge("observe", "supervisor")
    specialists = ["trajectory", "incident", "eta_impact", "route", "resource"]
    g.add_conditional_edges("supervisor", lambda s: s["next"],
                            {**{a: a for a in specialists}, "supervisor": "supervisor",
                             "recommend": "recommend", "end": END})
    for a in specialists:
        g.add_edge(a, "supervisor")
    g.add_edge("recommend", "human_approval")
    g.add_edge("human_approval", "act")
    g.add_edge("act", END)
    return g.compile(checkpointer=MemorySaver())


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def graph_mermaid() -> str:
    return get_graph().get_graph().draw_mermaid()


# ---------------------------------------------------------------------------
# Public API used by main.py
# ---------------------------------------------------------------------------
def _summary(thread_id: str) -> Dict[str, Any]:
    snap = get_graph().get_state({"configurable": {"thread_id": thread_id}})
    v = snap.values
    awaiting = bool(snap.next)
    rec = v.get("recommendation")
    if awaiting and rec:
        _PENDING[rec["id"]] = thread_id
    return {
        "thread_id": thread_id,
        "status": "awaiting_approval" if awaiting else (v.get("outcome") or "completed"),
        "trace": v.get("trace", []),
        "recommendation": rec,
        "findings": v.get("findings", {}),
    }


def run(mission_id: str) -> Dict[str, Any]:
    if not AGENTS_ENABLED:
        raise RuntimeError("langgraph is not installed")
    thread_id = f"run_{uuid.uuid4().hex[:10]}"
    get_graph().invoke({"mission_id": mission_id, "trace": []},
                       {"configurable": {"thread_id": thread_id}})
    return _summary(thread_id)


def resume(thread_id: str, decision: str) -> Dict[str, Any]:
    if not AGENTS_ENABLED:
        raise RuntimeError("langgraph is not installed")
    get_graph().invoke(Command(resume=decision), {"configurable": {"thread_id": thread_id}})
    return _summary(thread_id)


def resume_for_recommendation(rec_id: str, decision: str) -> Optional[Dict[str, Any]]:
    """If a paused agent run owns this recommendation, resume it and return
    its summary; otherwise None (caller applies/rejects directly)."""
    if not AGENTS_ENABLED:
        return None
    thread_id = _PENDING.get(rec_id)
    return resume(thread_id, decision) if thread_id else None
