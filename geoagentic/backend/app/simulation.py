"""
Vehicle movement simulation.

`tick()` is called on a timer (see main.py's background thread) and advances
every en-route vehicle along its active route's polyline based on elapsed
time and current speed (which incident handlers may have reduced). This
models GPS telemetry without needing real hardware; swap `tick()` for a
real GPS/ESP32 ingestion handler later and the rest of the pipeline (routes,
decision engine, dashboard) is unaffected.
"""
import time
from sqlalchemy.orm import Session
from . import models
from .routing import point_on_polyline_at_fraction, polyline_length_km

TICK_SECONDS = 2.0


def effective_speed_kmh(vehicle: models.Vehicle, route: models.RouteOption) -> float:
    speed = vehicle.base_speed_kmh
    if route.delay_min and route.base_eta_min:
        slowdown = route.base_eta_min / max(route.base_eta_min + route.delay_min, 0.01)
        speed *= slowdown
    if route.risk_score:
        speed *= max(0.5, 1 - route.risk_score * 0.3)
    return max(speed, 5.0)


def tick(db: Session):
    vehicles = db.query(models.Vehicle).filter(
        models.Vehicle.status.in_([models.VehicleStatus.enroute.value,
                                    models.VehicleStatus.off_route.value])
    ).all()
    for v in vehicles:
        if not v.active_route_id:
            continue
        route = db.query(models.RouteOption).get(v.active_route_id)
        if not route:
            continue
        speed = effective_speed_kmh(v, route)
        v.speed_kmh = round(speed, 1)
        dist_km = polyline_length_km(route.polyline) or 0.001
        frac_per_tick = (speed * (TICK_SECONDS / 3600)) / dist_km
        v.route_progress = min(1.0, (v.route_progress or 0) + frac_per_tick)

        p0 = [v.lat, v.lng]
        p1 = point_on_polyline_at_fraction(route.polyline, v.route_progress)
        v.heading_deg = _bearing(p0, p1)
        v.lat, v.lng = p1[0], p1[1]

        if v.route_progress >= 1.0:
            v.status = models.VehicleStatus.completed.value
            mission = db.query(models.Mission).get(v.current_mission_id)
            if mission:
                mission.status = "completed"
            db.add(models.TimelineEvent(
                mission_id=v.current_mission_id, vehicle_id=v.id,
                event_type="arrival", message=f"{v.callsign} arrived at destination."
            ))
    db.commit()


def _bearing(a, b):
    import math
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360
