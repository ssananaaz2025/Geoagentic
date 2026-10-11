"""Offline tests for the OSRM provider and the LangGraph agent team.
Run:  cd backend && pytest -q
No network needed: OSRM HTTP calls are mocked, routing defaults to mock."""
import os, sys, tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ROUTING_PROVIDER"] = "mock"       # agent tests stay offline
os.environ["ANTHROPIC_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi.testclient import TestClient
from app import config, decision_engine, live, routing, simulation, traffic_monitor
from app import main
from app.main import app

ORIGIN, DEST = (12.9716, 77.5946), (12.9352, 77.6245)


class FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def test_osrm_parsing_flips_lonlat_and_units(monkeypatch):
    seen = {}
    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        return FakeResp({"code": "Ok", "routes": [
            {"distance": 5200.0, "duration": 780.0,
             "geometry": {"coordinates": [[77.5946, 12.9716], [77.6, 12.95], [77.6245, 12.9352]]}},
            {"distance": 6100.0, "duration": 900.0,
             "geometry": {"coordinates": [[77.5946, 12.9716], [77.61, 12.94], [77.6245, 12.9352]]}}]})
    monkeypatch.setattr(routing.requests, "get", fake_get)
    monkeypatch.setattr(config, "OSRM_ENABLED", True)
    routes = routing._osrm_routes(ORIGIN, DEST)
    assert "77.5946,12.9716;77.6245,12.9352" in seen["url"]      # lon,lat in the URL
    assert routes[0]["polyline"][0] == [12.9716, 77.5946]        # lat,lng in our app
    assert routes[0]["distance_km"] == 5.2 and routes[0]["base_eta_min"] == 13.0
    assert [r["label"] for r in routes] == ["A", "B"] and not routes[0]["is_mock"]


def test_osrm_failure_falls_back_to_mock(monkeypatch):
    monkeypatch.setattr(config, "OSRM_ENABLED", True)
    monkeypatch.setattr(config, "ROUTING_PROVIDER", "osrm")
    monkeypatch.setattr(routing.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(routing.requests.RequestException("down")))
    routes, provider = routing.resolve_route_options(ORIGIN, DEST)
    assert provider == "mock" and len(routes) == 3 and routes[0]["is_mock"]


def test_osrm_used_in_auto_mode_without_google(monkeypatch):
    monkeypatch.setattr(config, "OSRM_ENABLED", True)
    monkeypatch.setattr(config, "ROUTING_PROVIDER", "auto")
    monkeypatch.setattr(config, "GOOGLE_MAPS_ENABLED", False)
    monkeypatch.setattr(routing.requests, "get", lambda *a, **k: FakeResp({"code": "Ok", "routes": [
        {"distance": 1000, "duration": 120, "geometry": {"coordinates": [[77.59, 12.97], [77.60, 12.96]]}}]}))
    assert routing.resolve_route_options(ORIGIN, DEST)[1] == "osrm"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c


def _mission(c):
    vid = next(v for v in c.get("/api/vehicles").json() if v["callsign"] == "AMB-01")["id"]
    m = c.post("/api/missions", json={"vehicle_id": vid, "origin_lat": ORIGIN[0], "origin_lng": ORIGIN[1],
                                      "dest_lat": DEST[0], "dest_lng": DEST[1]}).json()
    return vid, m


def test_status_reports_new_integrations(client):
    s = client.get("/api/config/status").json()
    assert s["langgraph"]["enabled"] and "provider_mode" in s["routing"]


def test_agent_graph_is_exposed(client):
    assert "supervisor" in client.get("/api/agents/graph").json()["mermaid"]


def test_nominal_mission_needs_no_action(client):
    _, m = _mission(client)
    r = client.post("/api/agents/run", json={"mission_id": m["id"]}).json()
    assert r["status"] == "no_action" and r["recommendation"] is None


def test_accident_reroute_pauses_for_human_then_applies(client):
    vid, m = _mission(client)
    active = next(x for x in m["routes"] if x["status"] == "active")
    mid = active["polyline"][len(active["polyline"]) * 6 // 10]
    # plain DB incident (no rule-engine recommendation) so the agents do the work
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    db.add(models.Incident(mission_id=m["id"], incident_type="accident", lat=mid[0], lng=mid[1],
                           radius_m=250, severity="high")); db.commit(); db.close()

    r = client.post("/api/agents/run", json={"mission_id": m["id"]}).json()
    assert r["status"] == "awaiting_approval"
    assert r["recommendation"]["action"] == "reroute" and r["recommendation"]["status"] == "pending"
    agents_seen = {t["agent"] for t in r["trace"]}
    assert {"observe", "supervisor", "trajectory", "incident", "eta_impact", "route", "recommend"} <= agents_seen
    # nothing applied yet
    veh = next(v for v in client.get("/api/vehicles").json() if v["id"] == vid)
    assert veh["active_route_id"] == active["id"]

    # dashboard "Apply" endpoint resumes the same graph run
    rec_id = r["recommendation"]["id"]
    out = client.post(f"/api/recommendations/{rec_id}/apply").json()
    assert out["status"] == "applied"
    veh = next(v for v in client.get("/api/vehicles").json() if v["id"] == vid)
    assert veh["active_route_id"] == out["recommended_route_id"] != active["id"]


def test_accept_reroute_replans_from_current_position_and_avoids_incident(client, monkeypatch):
    vid, mission = _mission(client)
    from app.database import SessionLocal
    from app import models

    current_position = (12.96, 77.55)
    db = SessionLocal()
    vehicle = db.query(models.Vehicle).get(vid)
    vehicle.lat, vehicle.lng = current_position
    vehicle.status = models.VehicleStatus.idle.value  # keep the simulated GPS still
    active = db.query(models.RouteOption).filter_by(
        mission_id=mission["id"], status="active"
    ).first()
    incident_point = active.polyline[len(active.polyline) * 6 // 10]
    db.commit()
    db.close()

    recommendations = client.post("/api/incidents", json={
        "mission_id": mission["id"],
        "incident_type": "roadblock",
        "lat": incident_point[0],
        "lng": incident_point[1],
        "radius_m": 250,
        "severity": "high",
    }).json()
    recommendation = next(r for r in recommendations if r["action"] == "reroute")

    def fake_routes(origin, dest, avg_speed_kmh=45.0):
        assert origin == current_position
        return [
            {
                "label": "A",
                "polyline": [list(origin), list(incident_point), list(dest)],
                "distance_km": 3.0,
                "base_eta_min": 2.0,
                "is_mock": False,
            },
            {
                "label": "B",
                "polyline": [list(origin), [12.96, 77.56], [12.93, 77.60], list(dest)],
                "distance_km": 6.0,
                "base_eta_min": 12.0,
                "is_mock": False,
            },
        ], "test"

    monkeypatch.setattr(decision_engine, "resolve_route_options", fake_routes)
    applied = client.post(
        f"/api/recommendations/{recommendation['id']}/apply"
    ).json()

    state = client.get("/api/state").json()
    active_vehicle = next(v for v in state["vehicles"] if v["id"] == vid)
    selected_route = next(
        r for m in state["missions"] for r in m["routes"]
        if r["id"] == active_vehicle["active_route_id"]
    )
    assert applied["status"] == "applied"
    assert applied["recommended_route_id"] == selected_route["id"]
    assert selected_route["label"] == "R1-B"  # the shorter route intersects the roadblock
    assert selected_route["polyline"][0] == list(current_position)
    assert selected_route["base_eta_min"] == 12.0
    assert applied["extra"]["reroute_origin"] == {
        "lat": current_position[0], "lng": current_position[1]
    }


def test_mass_casualty_stops_primary_and_dispatches_backup_to_scene(client, monkeypatch):
    primary_id, mission = _mission(client)
    scene = (12.9716, 77.5946)
    from app.database import SessionLocal
    from app import models

    def fake_routes(origin, dest, avg_speed_kmh=45.0):
        return [
            {"label": "A", "polyline": [list(origin), list(dest)],
             "distance_km": 3.0, "base_eta_min": 4.0, "is_mock": True},
            {"label": "B", "polyline": [list(origin), [12.96, 77.60], list(dest)],
             "distance_km": 4.0, "base_eta_min": 7.0, "is_mock": True},
        ], "test"

    monkeypatch.setattr(decision_engine, "resolve_route_options", fake_routes)
    recs = client.post("/api/incidents", json={
        "mission_id": mission["id"],
        "incident_type": "mass_casualty",
        "lat": scene[0],
        "lng": scene[1],
        "severity": "high",
    }).json()
    recommendation = next(rec for rec in recs if rec["action"] == "dispatch_backup")

    state = client.get("/api/state").json()
    primary = next(vehicle for vehicle in state["vehicles"] if vehicle["id"] == primary_id)
    backup = next(vehicle for vehicle in state["vehicles"] if vehicle["callsign"] == "AMB-02")
    assert primary["status"] == "at_incident"
    assert primary["speed_kmh"] == 0
    assert (primary["lat"], primary["lng"]) == scene
    assert backup["status"] == "idle"
    assert recommendation["extra"]["eta_min"] == 4.0

    applied = client.post(
        f"/api/recommendations/{recommendation['id']}/apply"
    ).json()
    state = client.get("/api/state").json()
    primary_after = next(vehicle for vehicle in state["vehicles"] if vehicle["id"] == primary_id)
    backup_after = next(vehicle for vehicle in state["vehicles"] if vehicle["callsign"] == "AMB-02")
    backup_mission = next(
        item for item in state["missions"] if item["id"] == backup_after["current_mission_id"]
    )
    backup_route = next(
        route for route in backup_mission["routes"] if route["id"] == backup_after["active_route_id"]
    )
    assert applied["status"] == "applied"
    assert primary_after["status"] == "at_incident"
    assert backup_after["status"] == "enroute"
    assert backup_mission["mission_type"] == "mass_casualty_backup"
    assert (backup_mission["dest_lat"], backup_mission["dest_lng"]) == scene
    assert backup_route["polyline"][0] == [backup_mission["origin_lat"], backup_mission["origin_lng"]]
    assert backup_route["polyline"][-1] == [*scene]

    db = SessionLocal()
    try:
        primary_before_tick = (primary_after["lat"], primary_after["lng"])
        backup_before_tick = (backup_after["lat"], backup_after["lng"])
        simulation.tick(db)
    finally:
        db.close()
    vehicles_after_tick = client.get("/api/state").json()["vehicles"]
    primary_after_tick = next(vehicle for vehicle in vehicles_after_tick if vehicle["id"] == primary_id)
    backup_after_tick = next(vehicle for vehicle in vehicles_after_tick if vehicle["id"] == backup_after["id"])
    assert primary_after_tick["status"] == "at_incident"
    assert (primary_after_tick["lat"], primary_after_tick["lng"]) == primary_before_tick
    assert backup_after_tick["status"] == "enroute"
    assert (backup_after_tick["lat"], backup_after_tick["lng"]) != backup_before_tick


def test_reject_via_resume_endpoint_changes_nothing(client):
    vid, m = _mission(client)
    active = next(x for x in m["routes"] if x["status"] == "active")
    mid = active["polyline"][len(active["polyline"]) * 6 // 10]
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    db.add(models.Incident(mission_id=m["id"], incident_type="roadblock", lat=mid[0], lng=mid[1],
                           radius_m=250, severity="high")); db.commit(); db.close()
    r = client.post("/api/agents/run", json={"mission_id": m["id"]}).json()
    res = client.post(f"/api/agents/runs/{r['thread_id']}/resume", json={"decision": "reject"}).json()
    assert res["status"] == "rejected"
    veh = next(v for v in client.get("/api/vehicles").json() if v["id"] == vid)
    assert veh["active_route_id"] == active["id"]


def _tomtom_incident(incident_id, geometry, category=6, severity=3, delay_s=900):
    points = []
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") == "LineString":
        points = coordinates
    elif geometry.get("type") == "Point":
        points = [coordinates]
    lng, lat = points[len(points) // 2]
    return {
        "id": incident_id,
        "external_id": incident_id,
        "lat": lat,
        "lng": lng,
        "incident_type": {6: "traffic", 8: "roadblock", 1: "accident"}.get(category, "hazard"),
        "severity": {0: "low", 1: "low", 2: "medium", 3: "high", 4: "high"}.get(severity, "high"),
        "description": "Mock TomTom incident",
        "delay_s": delay_s,
        "icon_category": category,
        "magnitude_of_delay": severity,
        "road_numbers": [],
        "geometry": geometry,
    }


def _line_on_route(route, start=3, end=6):
    return {
        "type": "LineString",
        "coordinates": [[lng, lat] for lat, lng in route["polyline"][start:end]],
    }


def _configure_monitor_routes(monkeypatch, vehicle, mission, active_route, alternative_points=None):
    origin = [vehicle["lat"], vehicle["lng"]]
    destination = [mission["dest_lat"], mission["dest_lng"]]
    active_line = active_route["polyline"]
    alternative_points = alternative_points or [
        origin,
        [origin[0] + 0.003, origin[1] + 0.001],
        destination,
    ]

    def fake_routes(route_origin, route_destination, avg_speed_kmh=45.0):
        return [
            {"label": "A", "polyline": active_line, "distance_km": active_route["distance_km"],
             "base_eta_min": active_route["base_eta_min"], "is_mock": False, "traffic_aware": False},
            {"label": "B", "polyline": alternative_points, "distance_km": 4.2,
             "base_eta_min": 1.0, "is_mock": False, "traffic_aware": True},
        ], "test-router"

    monkeypatch.setattr(traffic_monitor, "resolve_route_options", fake_routes)
    return fake_routes


def test_tomtom_incident_api_normalizes_success_and_empty_result(monkeypatch):
    monkeypatch.setattr(config, "TOMTOM_ENABLED", True)
    with live._cache_lock:
        live._cache.update(t=0.0, data=[])
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"], seen["params"], seen["timeout"] = url, params, timeout
        return FakeResp({"incidents": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[77.59, 12.97], [77.60, 12.96]]},
            "properties": {"id": "tt-1", "iconCategory": 6, "magnitudeOfDelay": 3,
                           "events": [{"description": "Congestion"}], "delay": 420,
                           "roadNumbers": ["NH-48"]},
        }]})

    monkeypatch.setattr(live.requests, "get", fake_get)
    data = live.fetch_live_incidents(force_refresh=True)
    assert data["status"] == "live" and len(data["incidents"]) == 1
    assert data["incidents"][0]["external_id"] == "tt-1"
    assert data["incidents"][0]["geometry"]["type"] == "LineString"
    assert data["incidents"][0]["lat"] == 12.96
    assert "traffic/services/5/incidentDetails" in seen["url"]
    assert "bbox" in seen["params"] and "geometry" in seen["params"]["fields"]

    monkeypatch.setattr(live.requests, "get", lambda *args, **kwargs: FakeResp({"incidents": []}))
    with live._cache_lock:
        live._cache.update(t=0.0, data=[])
    empty = live.fetch_live_incidents(force_refresh=True)
    assert empty["status"] == "live" and empty["incidents"] == []


@pytest.mark.parametrize("status,error", [(401, "invalid_key"), (403, "invalid_key"), (429, "rate_limited")])
def test_tomtom_auth_and_rate_limit_failures_are_explicit(monkeypatch, status, error):
    monkeypatch.setattr(config, "TOMTOM_ENABLED", True)
    with live._cache_lock:
        live._cache.update(t=0.0, data=[])

    class ErrorResponse:
        status_code = status

    class FailingResponse:
        def raise_for_status(self):
            raise live.requests.HTTPError(response=ErrorResponse())

    monkeypatch.setattr(live.requests, "get", lambda *args, **kwargs: FailingResponse())
    data = live.fetch_live_incidents(force_refresh=True)
    assert data["status"] == "unavailable" and data["error"] == error


def test_tomtom_missing_key_and_timeout_are_not_reported_as_live(monkeypatch):
    monkeypatch.setattr(config, "TOMTOM_ENABLED", False)
    assert live.fetch_live_incidents(force_refresh=True)["status"] == "unavailable"

    monkeypatch.setattr(config, "TOMTOM_ENABLED", True)
    monkeypatch.setattr(live.requests, "get",
                        lambda *args, **kwargs: (_ for _ in ()).throw(live.requests.Timeout()))
    with live._cache_lock:
        live._cache.update(t=0.0, data=[])
    data = live.fetch_live_incidents(force_refresh=True)
    assert data["status"] == "unavailable" and data["error"] == "timeout"


def test_route_monitor_matches_incident_geometry_and_ignores_unrelated_events(client, monkeypatch):
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
    active = next(route for route in mission["routes"] if route["status"] == "active")
    incident = _tomtom_incident("route-hit", _line_on_route(active))
    _configure_monitor_routes(monkeypatch, vehicle, mission, active)
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        db_mission = db.query(models.Mission).get(mission["id"])
        response = {"status": "live", "incidents": [incident], "checked_at": 1000.0}
        recommendations = traffic_monitor.monitor_mission(db, db_mission, response, 1000.0)
        db.commit()
        assert len(recommendations) == 1
        assert recommendations[0].action == "reroute"
        assert recommendations[0].extra["route_b"]["label"].endswith("-B")
        assert recommendations[0].extra["incident"]["source"] == "TomTom"
        assert recommendations[0].extra["route_evaluations"][0]["status"] == "affected"
        assert recommendations[0].extra["route_evaluations"][1]["status"] == "no_reported_incident"
        assert len(recommendations[0].extra["agent_trace"]) == 5

        outside = _tomtom_incident("elsewhere", {
            "type": "LineString", "coordinates": [[77.72, 13.08], [77.73, 13.07]]
        })
        response["incidents"] = [outside]
        response["checked_at"] = 1045.0
        assert traffic_monitor.monitor_mission(db, db_mission, response, 1045.0) == []
        db.commit()
        record = db.query(models.Incident).filter_by(external_id="route-hit").one()
        assert record.status == "resolved"
        assert record.freshness == "resolved"
        assert db.query(models.Recommendation).filter_by(mission_id=mission["id"]).count() == 1
    finally:
        db.close()


def test_monitor_deduplicates_changes_incident_clear_and_stale_status(client, monkeypatch):
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
    active = next(route for route in mission["routes"] if route["status"] == "active")
    _configure_monitor_routes(monkeypatch, vehicle, mission, active)
    incident = _tomtom_incident("dedupe", _line_on_route(active))
    response = {"status": "live", "incidents": [incident], "checked_at": 2000.0}
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        db_mission = db.query(models.Mission).get(mission["id"])
        first = traffic_monitor.monitor_mission(db, db_mission, response, 2000.0)
        db.commit()
        repeated = traffic_monitor.monitor_mission(
            db, db_mission, {**response, "checked_at": 2045.0}, 2045.0
        )
        db.commit()
        assert len(first) == 1 and repeated == []
        assert db.query(models.Incident).filter_by(
            mission_id=mission["id"], source="tomtom", external_id="dedupe"
        ).count() == 1

        worsened = _tomtom_incident("dedupe", _line_on_route(active), severity=4, delay_s=2400)
        changed = traffic_monitor.monitor_mission(
            db, db_mission, {"status": "live", "incidents": [worsened]}, 2090.0
        )
        db.commit()
        assert len(changed) == 1
        assert db.query(models.Incident).filter_by(
            mission_id=mission["id"], source="tomtom", external_id="dedupe"
        ).one().first_seen_at == 2000.0

        traffic_monitor.monitor_mission(
            db, db_mission, {"status": "live", "incidents": []}, 2135.0
        )
        db.commit()
        record = db.query(models.Incident).filter_by(external_id="dedupe").one()
        assert record.status == "resolved"

        stale_incident = _tomtom_incident("stale", _line_on_route(active))
        traffic_monitor.monitor_mission(
            db, db_mission, {"status": "live", "incidents": [stale_incident]}, 2180.0
        )
        db.commit()
        traffic_monitor.monitor_mission(
            db, db_mission, {"status": "stale", "error": "timeout", "incidents": [stale_incident]}, 2225.0
        )
        db.commit()
        stale_record = db.query(models.Incident).filter_by(external_id="stale").one()
        monitor_state = db.query(models.TrafficMonitorState).get(mission["id"])
        assert stale_record.status == "active" and stale_record.freshness == "stale"
        assert monitor_state.status == "stale"
    finally:
        db.close()


def test_monitor_handles_closure_and_separates_multiple_missions(client, monkeypatch):
    first_vehicle_id, first = _mission(client)
    vehicles = client.get("/api/vehicles").json()
    second_vehicle_id = next(v["id"] for v in vehicles if v["callsign"] == "AMB-02")
    second = client.post("/api/missions", json={
        "vehicle_id": second_vehicle_id, "origin_lat": 12.94, "origin_lng": 77.64,
        "dest_lat": 12.90, "dest_lng": 77.69,
    }).json()
    state = client.get("/api/state").json()
    first_vehicle = next(v for v in state["vehicles"] if v["id"] == first_vehicle_id)
    first_active = next(r for r in first["routes"] if r["status"] == "active")
    second_active = next(r for r in second["routes"] if r["status"] == "active")
    closure = _tomtom_incident("closed-road", _line_on_route(first_active), category=8)
    _configure_monitor_routes(monkeypatch, first_vehicle, first, first_active)
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        first_mission = db.query(models.Mission).get(first["id"])
        second_mission = db.query(models.Mission).get(second["id"])
        first_recs = traffic_monitor.monitor_mission(
            db, first_mission, {"status": "live", "incidents": [closure]}, 3000.0
        )
        # The same geometry does not match the separate active mission's route.
        second_recs = traffic_monitor.monitor_mission(
            db, second_mission, {"status": "live", "incidents": [closure]}, 3000.0
        )
        db.commit()
        assert first_recs and first_recs[0].action == "reroute"
        assert second_recs == []
        assert db.query(models.TrafficMonitorState).get(first["id"]).affected
        assert not db.query(models.TrafficMonitorState).get(second["id"]).affected
        assert second_active["status"] == "active"
    finally:
        db.close()


def test_approval_revalidates_tomtom_route_and_rejection_does_not_switch(client, monkeypatch):
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
    active = next(route for route in mission["routes"] if route["status"] == "active")
    _configure_monitor_routes(monkeypatch, vehicle, mission, active)
    incident = _tomtom_incident("approval-test", _line_on_route(active))
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        db_mission = db.query(models.Mission).get(mission["id"])
        rec = traffic_monitor.monitor_mission(
            db, db_mission, {"status": "live", "incidents": [incident]}, 4000.0
        )[0]
        db.commit()
        rec_id = rec.id
    finally:
        db.close()

    monkeypatch.setattr(traffic_monitor, "fetch_live_incidents", lambda force_refresh=False: {
        "status": "unavailable", "error": "timeout", "incidents": []
    })
    failed = client.post(f"/api/recommendations/{rec_id}/apply")
    assert failed.status_code == 409
    pending = next(r for r in client.get("/api/state").json()["recommendations"] if r["id"] == rec_id)
    assert pending["status"] == "pending"

    rejected = client.post(f"/api/recommendations/{rec_id}/reject").json()
    assert rejected["status"] == "rejected"
    current = next(v for v in client.get("/api/vehicles").json() if v["id"] == vehicle_id)
    assert current["active_route_id"] == active["id"]


def test_tomtom_approval_switches_route_only_after_fresh_clear_check(client, monkeypatch):
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
    active = next(route for route in mission["routes"] if route["status"] == "active")
    _configure_monitor_routes(monkeypatch, vehicle, mission, active)
    incident = _tomtom_incident("switch-test", _line_on_route(active))
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        db_mission = db.query(models.Mission).get(mission["id"])
        rec = traffic_monitor.monitor_mission(
            db, db_mission, {"status": "live", "incidents": [incident]}, 5000.0
        )[0]
        db.commit()
        rec_id = rec.id
    finally:
        db.close()

    monkeypatch.setattr(traffic_monitor, "fetch_live_incidents", lambda force_refresh=False: {
        "status": "live", "checked_at": 5030.0, "incidents": []
    })
    applied = client.post(f"/api/recommendations/{rec_id}/apply").json()
    current = next(v for v in client.get("/api/vehicles").json() if v["id"] == vehicle_id)
    assert applied["status"] == "applied"
    assert applied["extra"]["approval_validation"]["status"] == "live_clear"
    assert current["active_route_id"] == applied["recommended_route_id"] != active["id"]


@pytest.mark.parametrize("fresh_incident,expected_fragment", [
    ("closure", "road-closure"),
    ("congestion", "meaningful advantage"),
])
def test_approval_rejects_newly_blocked_or_no_longer_better_route(
    client, monkeypatch, fresh_incident, expected_fragment
):
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
    active = next(route for route in mission["routes"] if route["status"] == "active")
    alternative_points = [
        [vehicle["lat"], vehicle["lng"]],
        [vehicle["lat"] + 0.003, vehicle["lng"] + 0.001],
        [mission["dest_lat"], mission["dest_lng"]],
    ]
    _configure_monitor_routes(
        monkeypatch, vehicle, mission, active, alternative_points=alternative_points
    )
    if fresh_incident == "congestion":
        original_resolver = traffic_monitor.resolve_route_options

        def slightly_slower_route(*args, **kwargs):
            options, provider = original_resolver(*args, **kwargs)
            options[1]["base_eta_min"] = 8.0
            return options, provider

        monkeypatch.setattr(traffic_monitor, "resolve_route_options", slightly_slower_route)
    initial = _tomtom_incident(
        "approval-recheck", _line_on_route(active), severity=4, delay_s=1200
    )
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        db_mission = db.query(models.Mission).get(mission["id"])
        rec = traffic_monitor.monitor_mission(
            db, db_mission, {"status": "live", "incidents": [initial]}, 6000.0
        )[0]
        assert rec.action == "reroute"
        db.commit()
        rec_id = rec.id
        proposed = db.query(models.RouteOption).get(rec.recommended_route_id)
        assert proposed is not None
        alternative_lat, alternative_lng = alternative_points[1]
        fresh_geometry = (
            {"type": "LineString", "coordinates": [
                [alternative_lng, alternative_lat],
                [alternative_lng + 0.00004, alternative_lat + 0.00004],
            ]}
        )
    finally:
        db.close()

    if fresh_incident == "closure":
        new_event = _tomtom_incident("new-closure", fresh_geometry, category=8)
    else:
        new_event = _tomtom_incident(
            "new-congestion", fresh_geometry, category=6, severity=4, delay_s=2400
        )
    monkeypatch.setattr(traffic_monitor, "fetch_live_incidents", lambda force_refresh=False: {
        "status": "live", "checked_at": 6030.0, "incidents": [new_event]
    })
    db = SessionLocal()
    try:
        db_rec = db.query(models.Recommendation).get(rec_id)
        validation = traffic_monitor.validate_recommendation(db, db_rec)
        assert not validation[0], validation
    finally:
        db.close()
    response = client.post(f"/api/recommendations/{rec_id}/apply")
    assert response.status_code == 409
    assert expected_fragment in response.json()["detail"]
    pending = next(
        r for r in client.get("/api/state").json()["recommendations"] if r["id"] == rec_id
    )
    assert pending["status"] == "pending"
    current = next(v for v in client.get("/api/vehicles").json() if v["id"] == vehicle_id)
    assert current["active_route_id"] == active["id"]


def test_traffic_alert_runs_existing_agent_graph_and_reuses_its_recommendation(
    client, monkeypatch
):
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
    active = next(route for route in mission["routes"] if route["status"] == "active")
    _configure_monitor_routes(monkeypatch, vehicle, mission, active)
    incident = _tomtom_incident("agent-trigger", _line_on_route(active), severity=4, delay_s=1200)
    response = {"status": "live", "checked_at": 7000.0, "incidents": [incident]}
    monkeypatch.setattr(main, "fetch_live_incidents", lambda force_refresh=False: response)

    checked = client.post(f"/api/traffic/check/{mission['id']}")
    assert checked.status_code == 200
    recommendations = [
        rec for rec in client.get("/api/state").json()["recommendations"]
        if rec["mission_id"] == mission["id"] and rec["extra"].get("monitor_source") == "tomtom"
    ]
    assert len(recommendations) == 1
    recommendation = recommendations[0]
    assert recommendation["extra"]["agent_team"] is True
    agents = recommendation["extra"]["agent_trace"]
    assert {item["agent"] for item in agents} >= {"trajectory", "incident", "eta_impact", "route"}
    assert any(item["source"] == "TomTom" for item in recommendation["extra"]["findings"]["incident"]["affected"])
    monkeypatch.setattr(traffic_monitor, "fetch_live_incidents", lambda force_refresh=False: {
        "status": "unavailable", "error": "timeout", "incidents": []
    })
    rejected_as_stale = client.post(f"/api/recommendations/{recommendation['id']}/apply")
    assert rejected_as_stale.status_code == 409
    unchanged_vehicle = next(
        item for item in client.get("/api/vehicles").json() if item["id"] == vehicle_id
    )
    assert unchanged_vehicle["active_route_id"] == active["id"]


def test_deviation_produces_recovery_route(client):
    vid, m = _mission(client)
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    v = db.query(models.Vehicle).get(vid); v.lat += 0.01; db.commit(); db.close()   # ~1.1 km off
    r = client.post("/api/agents/run", json={"mission_id": m["id"]}).json()
    assert r["recommendation"]["action"] == "recovery_route"
    assert r["findings"]["trajectory"]["off_route"]


def test_preview_and_osrm_endpoints(client, monkeypatch):
    body = {"origin_lat": ORIGIN[0], "origin_lng": ORIGIN[1], "dest_lat": DEST[0], "dest_lng": DEST[1]}
    assert client.post("/api/routes/preview", json=body).json()["provider"] == "mock"
    assert client.post("/api/routes/osrm", json=body).status_code == 503   # disabled in mock mode


def _simple_explanation_facts(**overrides):
    facts = {
        "vehicle": "AMB-01",
        "destination": "the selected destination",
        "incident": {
            "source": "Simulated",
            "type": "accident",
            "description": "An accident was reported on the road.",
            "status": "active",
            "freshness": "current",
            "affects_current_route": True,
        },
        "current_route": {"label": "Route A", "eta_min": 32.0, "status": "affected"},
        "alternative_route": {
            "label": "Route B", "eta_min": 21.0, "status": "no_reported_incident",
            "available": True, "blocked": False,
        },
        "time_saved_min": 11.0,
        "action": "reroute",
        "impact": "The accident may delay travel on Route A.",
        "approval_status": "waiting",
        "traffic_status": "current",
    }
    facts.update(overrides)
    return facts


def test_accident_explanation_uses_vehicle_route_eta_and_simulated_source(client, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_ENABLED", False)
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
    active = next(route for route in mission["routes"] if route["status"] == "active")
    point = active["polyline"][len(active["polyline"]) * 6 // 10]
    rec = client.post("/api/incidents", json={
        "mission_id": mission["id"], "incident_type": "accident",
        "lat": point[0], "lng": point[1], "radius_m": 250, "severity": "high",
    }).json()[0]
    assert "What happened?" in rec["reason"]
    assert "Simulated" in rec["reason"]
    assert "accident" in rec["reason"].lower()
    assert vehicle["callsign"] in rec["reason"]
    assert active["label"] in rec["reason"]
    assert rec["extra"]["explanation_facts"]["incident"]["source"] == "Simulated"
    assert rec["extra"]["explanation_facts"]["vehicle"] == "AMB-01"


def test_fallback_explanation_compares_real_eta_and_names_alternative():
    explanation = decision_engine.explain(_simple_explanation_facts())
    assert "Route B" in explanation and "Route A" in explanation
    assert "21.0 minutes" in explanation and "32.0 minutes" in explanation
    assert "11.0 minutes" in explanation and "AMB-01" in explanation
    assert "waiting for dispatcher approval" in explanation


def test_fallback_explanation_rejects_closed_alternative_and_no_route():
    closed = decision_engine.explain(_simple_explanation_facts(
        alternative_route={"label": "Route B", "available": False, "blocked": True},
    ))
    assert "reported closure affects route Route B" in closed
    assert "should not be used" in closed
    unavailable = decision_engine.explain(_simple_explanation_facts(
        alternative_route={"available": False, "blocked": False},
        action="hold",
    ))
    assert "No alternative route could be verified" in unavailable
    assert "not enough current information" in unavailable


def test_no_tomtom_incidents_does_not_claim_a_route_is_clear(monkeypatch):
    monkeypatch.setattr(config, "TOMTOM_ENABLED", True)
    monkeypatch.setattr(live.requests, "get", lambda *args, **kwargs: FakeResp({"incidents": []}))
    with live._cache_lock:
        live._cache.update(t=0.0, data=[])
    response = live.fetch_live_incidents(force_refresh=True)
    assert response["status"] == "live" and response["incidents"] == []
    explanation = decision_engine.explain(_simple_explanation_facts(
        incident=None,
        alternative_route={"available": False},
        action="hold",
        traffic_status="current",
    ))
    assert "no alternative route could be verified" in explanation.lower()
    assert "clear" not in explanation.lower() and "safe" not in explanation.lower()


@pytest.mark.parametrize("traffic_status", ["unavailable", "stale"])
def test_stale_or_unavailable_traffic_explanation_is_cautious(traffic_status):
    explanation = decision_engine.explain(_simple_explanation_facts(
        incident={**_simple_explanation_facts()["incident"], "freshness": "stale"},
        traffic_status=traffic_status,
        approval_status="unverified",
    ))
    assert "cannot be confirmed" in explanation
    assert "Do not change routes based on unverified traffic" in explanation
    assert "fresh traffic information is available" in explanation


def test_llm_api_failure_uses_simple_structured_fallback(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_ENABLED", True)
    monkeypatch.setattr(
        decision_engine.requests, "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(decision_engine.requests.Timeout()),
    )
    explanation = decision_engine.explain(_simple_explanation_facts())
    assert "What happened?" in explanation
    assert "AMB-01" in explanation and "21.0 minutes" in explanation
    assert "waiting for dispatcher approval" in explanation


def test_llm_receives_structured_facts_and_accepts_simple_sections(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_ENABLED", True)
    captured = {}
    content = "\n".join([
        "What happened? A simulated accident affects Route A.",
        "Why does it matter? It may delay AMB-01.",
        "What do we recommend? Consider Route B for AMB-01.",
        "Why is this option better? Route B is 21.0 minutes, compared with Route A at 32.0 minutes. It saves 11.0 minutes.",
        "What should the dispatcher do? This recommendation is waiting for dispatcher approval.",
    ])

    class LlmResponse:
        def raise_for_status(self): pass
        def json(self): return {"content": [{"type": "text", "text": content}]}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, body=json)
        return LlmResponse()

    monkeypatch.setattr(decision_engine.requests, "post", fake_post)
    facts = _simple_explanation_facts()
    explanation = decision_engine.explain(facts)
    assert explanation == content
    assert "AMB-01" in captured["body"]["messages"][0]["content"]
    assert "32.0" in captured["body"]["messages"][0]["content"]
    assert captured["headers"]["x-api-key"] == config.ANTHROPIC_API_KEY


@pytest.mark.parametrize("decision,status_text", [
    ("apply", "The dispatcher approved the change."),
    ("reject", "The dispatcher rejected this recommendation."),
])
def test_approval_and_rejection_update_dispatcher_explanation(client, monkeypatch, decision, status_text):
    monkeypatch.setattr(config, "ANTHROPIC_ENABLED", False)
    vehicle_id, mission = _mission(client)
    state = client.get("/api/state").json()
    active = next(route for route in mission["routes"] if route["status"] == "active")
    point = active["polyline"][len(active["polyline"]) * 6 // 10]
    created = client.post("/api/incidents", json={
        "mission_id": mission["id"], "incident_type": "accident",
        "lat": point[0], "lng": point[1], "radius_m": 250, "severity": "high",
    }).json()[0]
    endpoint = "apply" if decision == "apply" else "reject"
    result = client.post(f"/api/recommendations/{created['id']}/{endpoint}")
    assert result.status_code == 200
    assert status_text in result.json()["reason"]
    assert result.json()["status"] == ("applied" if decision == "apply" else "rejected")
