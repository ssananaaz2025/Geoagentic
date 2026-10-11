"""
SQLAlchemy ORM models for GeoAgentic.

Entity map:
  Vehicle       -- an ambulance / fire engine / police unit
  Mission       -- one dispatch job: origin -> destination for a vehicle
  RouteOption   -- route A/B/C candidates generated for a mission
  Incident      -- injected or external traffic event, plus its freshness metadata
  Recommendation-- a GeoAgent decision tied to an incident, pending dispatcher approval
  TimelineEvent -- append-only log shown in the dashboard timeline
"""
import enum
import uuid
import time
from sqlalchemy import (
    Column, String, Float, Integer, Boolean, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship
from .database import Base


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class VehicleType(str, enum.Enum):
    ambulance = "ambulance"
    fire = "fire"
    police = "police"


class VehicleStatus(str, enum.Enum):
    idle = "idle"
    enroute = "enroute"
    off_route = "off_route"
    at_incident = "at_incident"
    completed = "completed"


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(String, primary_key=True, default=lambda: gen_id("veh"))
    callsign = Column(String, unique=True, nullable=False)          # AMB-01
    vehicle_type = Column(String, nullable=False)                    # ambulance/fire/police
    status = Column(String, default=VehicleStatus.idle.value)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    heading_deg = Column(Float, default=0.0)
    speed_kmh = Column(Float, default=45.0)
    base_speed_kmh = Column(Float, default=45.0)
    current_mission_id = Column(String, ForeignKey("missions.id"), nullable=True)
    active_route_id = Column(String, ForeignKey("route_options.id"), nullable=True)
    route_progress = Column(Float, default=0.0)   # 0..1 fraction along the active route
    off_route = Column(Boolean, default=False)
    deviation_m = Column(Float, default=0.0)
    created_at = Column(Float, default=time.time)


class Mission(Base):
    __tablename__ = "missions"

    id = Column(String, primary_key=True, default=lambda: gen_id("mis"))
    vehicle_id = Column(String, ForeignKey("vehicles.id"), nullable=False)
    mission_type = Column(String, default="emergency_response")
    origin_lat = Column(Float, nullable=False)
    origin_lng = Column(Float, nullable=False)
    dest_lat = Column(Float, nullable=False)
    dest_lng = Column(Float, nullable=False)
    status = Column(String, default="active")   # active/completed/cancelled
    created_at = Column(Float, default=time.time)


class RouteOption(Base):
    __tablename__ = "route_options"

    id = Column(String, primary_key=True, default=lambda: gen_id("rte"))
    mission_id = Column(String, ForeignKey("missions.id"), nullable=False)
    label = Column(String, nullable=False)          # A / B / C / RECOVERY
    polyline = Column(JSON, nullable=False)          # [[lat,lng], ...]
    distance_km = Column(Float, nullable=False)
    base_eta_min = Column(Float, nullable=False)
    delay_min = Column(Float, default=0.0)           # extra delay injected by incidents
    risk_score = Column(Float, default=0.0)           # 0..1, higher = riskier
    status = Column(String, default="available")     # available/active/blocked
    is_mock = Column(Boolean, default=True)           # True until a real routing API is wired in
    traffic_aware = Column(Boolean, default=False)
    traffic_status = Column(String, default="unknown")  # unknown/clear/affected/unverified


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(String, primary_key=True, default=lambda: gen_id("inc"))
    mission_id = Column(String, ForeignKey("missions.id"), nullable=True)
    incident_type = Column(String, nullable=False)   # accident/traffic/roadblock/flood/hazard/route_deviation/mass_casualty
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    radius_m = Column(Float, default=250.0)
    severity = Column(String, default="high")         # low/medium/high
    status = Column(String, default="active")          # active/resolved
    affected_route_id = Column(String, ForeignKey("route_options.id"), nullable=True)
    source = Column(String, default="simulation")
    external_id = Column(String, nullable=True)
    geometry = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    delay_s = Column(Float, nullable=True)
    icon_category = Column(Integer, nullable=True)
    source_data = Column(JSON, nullable=True)
    first_seen_at = Column(Float, default=time.time)
    last_seen_at = Column(Float, default=time.time)
    last_checked_at = Column(Float, default=time.time)
    freshness = Column(String, default="current")     # current/stale/resolved
    created_at = Column(Float, default=time.time)


class TrafficMonitorState(Base):
    __tablename__ = "traffic_monitor_states"

    mission_id = Column(String, ForeignKey("missions.id"), primary_key=True)
    route_id = Column(String, nullable=True)
    status = Column(String, default="pending")       # live/unavailable/stale
    affected = Column(Boolean, default=False)
    provider = Column(String, nullable=True)
    last_checked_at = Column(Float, nullable=True)
    last_error = Column(Text, nullable=True)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(String, primary_key=True, default=lambda: gen_id("rec"))
    mission_id = Column(String, ForeignKey("missions.id"), nullable=False)
    incident_id = Column(String, ForeignKey("incidents.id"), nullable=True)
    recommended_route_id = Column(String, ForeignKey("route_options.id"), nullable=True)
    action = Column(String, nullable=False)      # reroute / hold / dispatch_backup / recovery_route
    reason = Column(Text, nullable=False)
    confidence = Column(Float, default=0.7)
    extra = Column(JSON, nullable=True)           # e.g. backup vehicle id, comparison table
    status = Column(String, default="pending")    # pending/applied/rejected
    created_at = Column(Float, default=time.time)


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id = Column(String, primary_key=True, default=lambda: gen_id("evt"))
    mission_id = Column(String, ForeignKey("missions.id"), nullable=True)
    vehicle_id = Column(String, ForeignKey("vehicles.id"), nullable=True)
    event_type = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    timestamp = Column(Float, default=time.time)
