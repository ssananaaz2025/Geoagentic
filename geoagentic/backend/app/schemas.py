"""Pydantic request/response schemas."""
from typing import Optional, List, Any
from pydantic import BaseModel, field_validator

from . import config

_B = config.BENGALURU_BOUNDS


def _check_lat(v: Optional[float]) -> Optional[float]:
    """Reject latitudes outside Bengaluru urban limits (see
    config.BENGALURU_BOUNDS). This demo is scoped to Bengaluru only."""
    if v is not None and not (_B["min_lat"] <= v <= _B["max_lat"]):
        raise ValueError(
            f"latitude {v} is outside Bengaluru urban limits "
            f"({_B['min_lat']}\u2013{_B['max_lat']}). This demo only supports locations within Bengaluru."
        )
    return v


def _check_lng(v: Optional[float]) -> Optional[float]:
    """Reject longitudes outside Bengaluru urban limits (see
    config.BENGALURU_BOUNDS). This demo is scoped to Bengaluru only."""
    if v is not None and not (_B["min_lng"] <= v <= _B["max_lng"]):
        raise ValueError(
            f"longitude {v} is outside Bengaluru urban limits "
            f"({_B['min_lng']}\u2013{_B['max_lng']}). This demo only supports locations within Bengaluru."
        )
    return v


class VehicleCreate(BaseModel):
    callsign: str
    vehicle_type: str  # ambulance/fire/police
    lat: float
    lng: float
    speed_kmh: Optional[float] = 45.0

    @field_validator("lat")
    @classmethod
    def _validate_lat(cls, v):
        return _check_lat(v)

    @field_validator("lng")
    @classmethod
    def _validate_lng(cls, v):
        return _check_lng(v)


class VehicleOut(BaseModel):
    id: str
    callsign: str
    vehicle_type: str
    status: str
    lat: float
    lng: float
    heading_deg: float
    speed_kmh: float
    current_mission_id: Optional[str] = None
    active_route_id: Optional[str] = None
    route_progress: float
    off_route: bool
    deviation_m: float

    class Config:
        from_attributes = True


class MissionCreate(BaseModel):
    vehicle_id: str
    mission_type: Optional[str] = "emergency_response"
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float

    @field_validator("origin_lat", "dest_lat")
    @classmethod
    def _validate_lat(cls, v):
        return _check_lat(v)

    @field_validator("origin_lng", "dest_lng")
    @classmethod
    def _validate_lng(cls, v):
        return _check_lng(v)


class RouteOut(BaseModel):
    id: str
    mission_id: str
    label: str
    polyline: List[List[float]]
    distance_km: float
    base_eta_min: float
    delay_min: float
    risk_score: float
    status: str
    is_mock: bool
    traffic_aware: bool = False
    traffic_status: str = "unknown"

    class Config:
        from_attributes = True


class MissionOut(BaseModel):
    id: str
    vehicle_id: str
    mission_type: str
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float
    status: str
    routes: List[RouteOut] = []

    class Config:
        from_attributes = True


class IncidentCreate(BaseModel):
    mission_id: Optional[str] = None
    incident_type: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_m: Optional[float] = 250.0
    severity: Optional[str] = "high"

    @field_validator("lat")
    @classmethod
    def _validate_lat(cls, v):
        return _check_lat(v)

    @field_validator("lng")
    @classmethod
    def _validate_lng(cls, v):
        return _check_lng(v)


class IncidentOut(BaseModel):
    id: str
    mission_id: Optional[str]
    incident_type: str
    lat: Optional[float]
    lng: Optional[float]
    radius_m: float
    severity: str
    status: str
    affected_route_id: Optional[str]
    source: str = "simulation"
    external_id: Optional[str] = None
    geometry: Optional[Any] = None
    description: Optional[str] = None
    first_seen_at: Optional[float] = None
    last_seen_at: Optional[float] = None
    last_checked_at: Optional[float] = None
    freshness: str = "current"
    delay_s: Optional[float] = None
    icon_category: Optional[int] = None
    source_data: Optional[Any] = None

    class Config:
        from_attributes = True


class RecommendationOut(BaseModel):
    id: str
    mission_id: str
    incident_id: Optional[str]
    recommended_route_id: Optional[str]
    action: str
    reason: str
    confidence: float
    extra: Optional[Any] = None
    status: str

    class Config:
        from_attributes = True


class TimelineOut(BaseModel):
    id: str
    mission_id: Optional[str]
    vehicle_id: Optional[str]
    event_type: str
    message: str
    timestamp: float

    class Config:
        from_attributes = True


class ApplyRecommendation(BaseModel):
    recommendation_id: str


class DeviationTrigger(BaseModel):
    mission_id: str
    offset_m: Optional[float] = 400.0


class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float

    @field_validator("origin_lat", "dest_lat")
    @classmethod
    def _v_lat(cls, v):
        return _check_lat(v)

    @field_validator("origin_lng", "dest_lng")
    @classmethod
    def _v_lng(cls, v):
        return _check_lng(v)


class AgentRunRequest(BaseModel):
    mission_id: str


class AgentResume(BaseModel):
    decision: str   # "approve" | "reject"
