"""
GeoAgentic backend — FastAPI application.

Run:
    uvicorn app.main:app --reload --port 8000

The app seeds a small demo scenario on first startup (three vehicles in an
Indian city grid) so the frontend has something to show immediately. All
state lives in SQLite (geoagentic.db) via SQLAlchemy; delete that file (or
call POST /api/reset) to start clean.
"""
import threading
import time
import logging
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import models, schemas, decision_engine, simulation, config, agents, traffic_monitor
from .database import Base, engine, get_db, SessionLocal
from . import database
from .routing import generate_route_options, resolve_route_options
from .live import fetch_live_incidents, router as live_router

Base.metadata.create_all(bind=engine)
database.migrate_sqlite_schema()

app = FastAPI(title="GeoAgentic API", version="0.2.0")

app.include_router(live_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Background simulation loop
# ---------------------------------------------------------------------------
_stop_event = threading.Event()
logger = logging.getLogger("geoagentic.main")


def _simulation_loop():
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            simulation.tick(db)
        except Exception as exc:  # keep the loop alive even if one tick errors
            print(f"[simulation] tick error: {exc}")
        finally:
            db.close()
        time.sleep(simulation.TICK_SECONDS)


def _traffic_monitor_loop():
    while not _stop_event.is_set():
        if _stop_event.wait(config.TOMTOM_MONITOR_INTERVAL_S):
            return
        db = SessionLocal()
        try:
            traffic_monitor.monitor_active_missions(db)
        except Exception:
            logger.exception("TomTom route monitor cycle failed")
        finally:
            db.close()


@app.on_event("startup")
def on_startup():
    db = SessionLocal()
    try:
        _seed_if_empty(db)
    finally:
        db.close()
    t = threading.Thread(target=_simulation_loop, daemon=True)
    t.start()
    monitor_thread = threading.Thread(target=_traffic_monitor_loop, daemon=True)
    monitor_thread.start()


@app.on_event("shutdown")
def on_shutdown():
    _stop_event.set()


def _seed_if_empty(db: Session):
    if db.query(models.Vehicle).count() > 0:
        return
    # Bengaluru-ish demo coordinates
    demo = [
        ("AMB-01", "ambulance", 12.9716, 77.5946),
        ("FIRE-01", "fire", 12.9800, 77.6050),
        ("POL-01", "police", 12.9650, 77.5900),
        ("AMB-02", "ambulance", 12.9550, 77.6100),
    ]
    for callsign, vtype, lat, lng in demo:
        db.add(models.Vehicle(callsign=callsign, vehicle_type=vtype, lat=lat, lng=lng))
    db.commit()
    db.add(models.TimelineEvent(event_type="system", message="GeoAgentic control room initialized."))
    db.commit()


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------
@app.get("/api/vehicles", response_model=List[schemas.VehicleOut])
def list_vehicles(db: Session = Depends(get_db)):
    return db.query(models.Vehicle).all()


@app.post("/api/vehicles", response_model=schemas.VehicleOut)
def create_vehicle(payload: schemas.VehicleCreate, db: Session = Depends(get_db)):
    if payload.vehicle_type not in ("ambulance", "fire", "police"):
        raise HTTPException(400, "vehicle_type must be ambulance, fire, or police")
    callsign = payload.callsign.strip()
    if not callsign:
        raise HTTPException(422, "Callsign is required")
    if db.query(models.Vehicle).filter(models.Vehicle.callsign == callsign).first():
        raise HTTPException(409, f"A vehicle called '{callsign}' already exists - pick another callsign")
    data = payload.dict()
    data["callsign"] = callsign
    v = models.Vehicle(**data)
    v.base_speed_kmh = payload.speed_kmh or 45.0
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# Missions (Dispatch & Trip Setup)
# ---------------------------------------------------------------------------
@app.post("/api/missions", response_model=schemas.MissionOut)
def create_mission(payload: schemas.MissionCreate, db: Session = Depends(get_db)):
    vehicle = db.query(models.Vehicle).get(payload.vehicle_id)
    if not vehicle:
        raise HTTPException(404, "vehicle not found")

    mission = models.Mission(**payload.dict())
    db.add(mission)
    db.flush()

    options = generate_route_options(
        (payload.origin_lat, payload.origin_lng),
        (payload.dest_lat, payload.dest_lng),
        avg_speed_kmh=vehicle.base_speed_kmh,
    )
    routes = []
    for i, opt in enumerate(options):
        r = models.RouteOption(
            mission_id=mission.id, label=opt["label"], polyline=opt["polyline"],
            distance_km=opt["distance_km"], base_eta_min=opt["base_eta_min"],
            status="active" if i == 0 else "available",
            is_mock=opt.get("is_mock", True),
            traffic_aware=opt.get("traffic_aware", False),
        )
        db.add(r)
        routes.append(r)
    db.flush()

    vehicle.current_mission_id = mission.id
    vehicle.active_route_id = routes[0].id
    vehicle.status = models.VehicleStatus.enroute.value
    vehicle.lat, vehicle.lng = payload.origin_lat, payload.origin_lng
    vehicle.route_progress = 0.0
    vehicle.off_route = False

    db.add(models.TimelineEvent(
        mission_id=mission.id, vehicle_id=vehicle.id, event_type="dispatch",
        message=f"{vehicle.callsign} dispatched. Mission '{mission.mission_type}' started on route A."
    ))
    db.commit()
    db.refresh(mission)
    mission.routes = routes
    return mission


@app.get("/api/missions/{mission_id}", response_model=schemas.MissionOut)
def get_mission(mission_id: str, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).get(mission_id)
    if not mission:
        raise HTTPException(404, "mission not found")
    mission.routes = db.query(models.RouteOption).filter_by(mission_id=mission_id).all()
    return mission


# ---------------------------------------------------------------------------
# Incidents -> GeoAgent recommendations
# ---------------------------------------------------------------------------
@app.post("/api/incidents", response_model=List[schemas.RecommendationOut])
def create_incident(payload: schemas.IncidentCreate, db: Session = Depends(get_db)):
    incident = models.Incident(**payload.dict())
    db.add(incident)
    db.flush()

    recs = []

    if payload.incident_type == "hazard" and not payload.mission_id:
        active_missions = db.query(models.Mission).filter_by(status="active").all()
        for m in active_missions:
            vehicle = db.query(models.Vehicle).get(m.vehicle_id)
            rec = decision_engine.handle_hazard(db, m, incident, vehicle)
            recs.append(rec)
    else:
        if not payload.mission_id:
            raise HTTPException(400, "mission_id is required for this incident type")
        mission = db.query(models.Mission).get(payload.mission_id)
        if not mission:
            raise HTTPException(404, "mission not found")
        incident.mission_id = mission.id
        vehicle = db.query(models.Vehicle).get(mission.vehicle_id)

        if payload.incident_type == "accident":
            recs.append(decision_engine.handle_accident(db, mission, incident))
        elif payload.incident_type == "traffic":
            recs.append(decision_engine.handle_traffic(db, mission, incident))
        elif payload.incident_type == "roadblock":
            recs.append(decision_engine.handle_roadblock(db, mission, incident))
        elif payload.incident_type == "flood":
            recs.append(decision_engine.handle_flood(db, mission, incident))
        elif payload.incident_type == "hazard":
            recs.append(decision_engine.handle_hazard(db, mission, incident, vehicle))
        elif payload.incident_type == "mass_casualty":
            recs.append(decision_engine.handle_mass_casualty(db, mission, incident, db))
        else:
            raise HTTPException(400, f"unknown incident_type '{payload.incident_type}'")

    db.commit()
    for r in recs:
        db.refresh(r)
    return recs


@app.post("/api/vehicles/{vehicle_id}/deviate", response_model=List[schemas.RecommendationOut])
def trigger_deviation(vehicle_id: str, offset_m: float = 400.0, db: Session = Depends(get_db)):
    """Scenario 6: nudge the vehicle's simulated GPS off its planned corridor
    and let the GeoAgent detect + recover."""
    vehicle = db.query(models.Vehicle).get(vehicle_id)
    if not vehicle or not vehicle.current_mission_id:
        raise HTTPException(404, "vehicle not found or has no active mission")
    mission = db.query(models.Mission).get(vehicle.current_mission_id)

    # Displace perpendicular to current heading by offset_m
    import math
    heading_rad = math.radians(vehicle.heading_deg + 90)
    d_deg_lat = (offset_m / 111320.0) * math.cos(heading_rad)
    d_deg_lng = (offset_m / (111320.0 * math.cos(math.radians(vehicle.lat)) or 1)) * math.sin(heading_rad)
    vehicle.lat += d_deg_lat
    vehicle.lng += d_deg_lng

    rec = decision_engine.handle_route_deviation(db, mission, vehicle)
    db.commit()
    if rec:
        db.refresh(rec)
        return [rec]
    return []


@app.post("/api/recommendations/{rec_id}/apply", response_model=schemas.RecommendationOut)
def apply_recommendation(rec_id: str, db: Session = Depends(get_db)):
    rec = db.query(models.Recommendation).get(rec_id)
    if not rec:
        raise HTTPException(404, "recommendation not found")
    if rec.status != "pending":
        raise HTTPException(409, f"recommendation is already {rec.status}")
    # If a LangGraph run is paused on this recommendation, resume it so the
    # graph's `act` node applies it; otherwise apply directly.
    try:
        if agents.resume_for_recommendation(rec_id, "approve") is not None:
            db.expire_all()
            return db.query(models.Recommendation).get(rec_id)
        return decision_engine.apply_recommendation(db, rec)
    except decision_engine.RouteValidationError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/recommendations/{rec_id}/reject", response_model=schemas.RecommendationOut)
def reject_recommendation(rec_id: str, db: Session = Depends(get_db)):
    rec = db.query(models.Recommendation).get(rec_id)
    if not rec:
        raise HTTPException(404, "recommendation not found")
    if agents.resume_for_recommendation(rec_id, "reject") is not None:
        db.expire_all()
        return db.query(models.Recommendation).get(rec_id)
    return decision_engine.reject_recommendation(db, rec)


# ---------------------------------------------------------------------------
# Routing (OSRM / provider-aware)
# ---------------------------------------------------------------------------
@app.post("/api/routes/osrm")
def osrm_routes(payload: schemas.RouteRequest):
    """Routes from OSRM (OpenStreetMap road network) for an origin/destination.
    OSRM has no live traffic, so durations are free-flow."""
    from .routing import _osrm_routes
    if not config.OSRM_ENABLED:
        raise HTTPException(503, "OSRM is disabled (OSRM_ENABLED / ROUTING_PROVIDER)")
    routes = _osrm_routes((payload.origin_lat, payload.origin_lng),
                          (payload.dest_lat, payload.dest_lng))
    if not routes:
        raise HTTPException(502, "OSRM returned no route (see server logs)")
    return {"provider": "osrm", "routes": routes}


@app.post("/api/routes/preview")
def preview_routes(payload: schemas.RouteRequest):
    """Routes from whichever provider ROUTING_PROVIDER selects (google/osrm/mock)."""
    routes, provider = resolve_route_options(
        (payload.origin_lat, payload.origin_lng), (payload.dest_lat, payload.dest_lng))
    return {"provider": provider, "routes": routes}


# ---------------------------------------------------------------------------
# LangGraph multi-agent team
# ---------------------------------------------------------------------------
def _require_agents():
    if not agents.AGENTS_ENABLED:
        raise HTTPException(503, "LangGraph is not installed: pip install langgraph")


@app.post("/api/agents/run")
def agents_run(payload: schemas.AgentRunRequest, db: Session = Depends(get_db)):
    """Run the supervisor + specialist agents for a mission. Returns the
    trace and a *pending* recommendation; the graph then waits for the
    operator (POST .../resume, or the normal Apply/Dismiss endpoints)."""
    _require_agents()
    if not db.query(models.Mission).get(payload.mission_id):
        raise HTTPException(404, "mission not found")
    return agents.run(payload.mission_id)


@app.post("/api/agents/runs/{thread_id}/resume")
def agents_resume(thread_id: str, payload: schemas.AgentResume):
    _require_agents()
    if payload.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    try:
        return agents.resume(thread_id, payload.decision)
    except decision_engine.RouteValidationError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(404, f"no resumable run '{thread_id}': {exc}")


@app.get("/api/agents/graph")
def agents_graph():
    """Mermaid source of the agent graph (paste into mermaid.live)."""
    _require_agents()
    return {"mermaid": agents.graph_mermaid()}


# ---------------------------------------------------------------------------
# Timeline + full-state snapshot (polled by the dashboard every ~2s)
# ---------------------------------------------------------------------------
@app.get("/api/timeline", response_model=List[schemas.TimelineOut])
def get_timeline(mission_id: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(models.TimelineEvent)
    if mission_id:
        q = q.filter_by(mission_id=mission_id)
    return q.order_by(models.TimelineEvent.timestamp.desc()).limit(limit).all()


@app.get("/api/state")
def get_state(db: Session = Depends(get_db)):
    """Single snapshot endpoint the dashboard polls: all vehicles, active
    missions with their routes, active incidents, pending recommendations,
    and the recent timeline. Keeps the frontend to one HTTP round trip."""
    vehicles = db.query(models.Vehicle).all()
    missions = db.query(models.Mission).filter_by(status="active").all()
    mission_ids = [m.id for m in missions]
    routes = db.query(models.RouteOption).filter(models.RouteOption.mission_id.in_(mission_ids)).all() if mission_ids else []
    incidents = db.query(models.Incident).filter_by(status="active").all()
    recs = db.query(models.Recommendation).order_by(models.Recommendation.created_at.desc()).limit(20).all()
    timeline = db.query(models.TimelineEvent).order_by(models.TimelineEvent.timestamp.desc()).limit(40).all()
    monitor_states = db.query(models.TrafficMonitorState).all()

    def m_out(m):
        d = schemas.MissionOut.from_orm(m).dict()
        d["routes"] = [schemas.RouteOut.from_orm(r).dict() for r in routes if r.mission_id == m.id]
        return d

    return {
        "server_time": time.time(),   # lets the UI compute "x s ago" without clock skew
        "vehicles": [schemas.VehicleOut.from_orm(v).dict() for v in vehicles],
        "missions": [m_out(m) for m in missions],
        "incidents": [schemas.IncidentOut.from_orm(i).dict() for i in incidents],
        "recommendations": [schemas.RecommendationOut.from_orm(r).dict() for r in recs],
        "timeline": [schemas.TimelineOut.from_orm(t).dict() for t in timeline],
        "traffic_monitor": [{
            "mission_id": item.mission_id,
            "route_id": item.route_id,
            "status": item.status,
            "affected": item.affected,
            "provider": item.provider,
            "last_checked_at": item.last_checked_at,
            "last_error": item.last_error,
        } for item in monitor_states],
    }


@app.post("/api/reset")
def reset_all(db: Session = Depends(get_db)):
    for tbl in [models.TimelineEvent, models.TrafficMonitorState, models.Recommendation, models.Incident,
                models.RouteOption, models.Mission, models.Vehicle]:
        db.query(tbl).delete()
    db.commit()
    _seed_if_empty(db)
    return {"status": "reset"}


@app.post("/api/traffic/check/{mission_id}")
def check_mission_traffic(mission_id: str, db: Session = Depends(get_db)):
    """Force a fresh TomTom poll and route evaluation for one active mission."""
    mission = db.query(models.Mission).get(mission_id)
    if not mission:
        raise HTTPException(404, "mission not found")
    response = fetch_live_incidents(force_refresh=True)
    if response.get("status") != "live":
        raise HTTPException(503, f"TomTom traffic data is {response.get('status')}: "
                                 f"{response.get('error', 'no fresh response')}")
    recommendations = traffic_monitor.monitor_mission(
        db, mission, response, response.get("checked_at")
    )
    db.commit()
    if recommendations:
        traffic_monitor.start_agent_workflow(mission.id)
    return {
        "status": "live",
        "checked_at": response.get("checked_at"),
        "recommendations": [schemas.RecommendationOut.from_orm(rec).dict()
                            for rec in recommendations],
    }


@app.get("/api/traffic/status")
def traffic_status(db: Session = Depends(get_db)):
    return {
        "enabled": config.TOMTOM_ENABLED,
        "poll_interval_s": config.TOMTOM_MONITOR_INTERVAL_S,
        "missions": [{
            "mission_id": item.mission_id,
            "route_id": item.route_id,
            "status": item.status,
            "affected": item.affected,
            "provider": item.provider,
            "last_checked_at": item.last_checked_at,
            "last_error": item.last_error,
        } for item in db.query(models.TrafficMonitorState).all()],
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "time": time.time()}


@app.get("/api/config/status")
def config_status():
    """Integration status for the Admin dashboard — booleans/labels only,
    never the actual API keys."""
    return {
        "google_maps": {
            "enabled": config.GOOGLE_MAPS_ENABLED,
            "region": config.GOOGLE_MAPS_REGION,
        },
        "routing": {
            "provider_mode": config.ROUTING_PROVIDER,
            "osrm_enabled": config.OSRM_ENABLED,
            "osrm_base_url": config.OSRM_BASE_URL if config.OSRM_ENABLED else None,
        },
        "tomtom": {"enabled": config.TOMTOM_ENABLED,
                   "monitor_interval_s": config.TOMTOM_MONITOR_INTERVAL_S},
        "langgraph": {"enabled": agents.AGENTS_ENABLED},
        "claude_llm": {
            "enabled": config.ANTHROPIC_ENABLED,
            "model": config.CLAUDE_MODEL if config.ANTHROPIC_ENABLED else None,
        },
        "database_url_scheme": database.DATABASE_URL.split("://")[0] if "://" in database.DATABASE_URL else database.DATABASE_URL,
    }
