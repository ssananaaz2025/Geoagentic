-- =============================================================================
-- GeoAgentic — reference relational schema
-- =============================================================================
-- The backend runs on SQLite by default via SQLAlchemy (zero setup), which
-- creates these tables automatically on first run. This file is the portable
-- SQL equivalent — use it to stand the schema up directly on Postgres/MySQL,
-- or as documentation of the data model. Adjust types (e.g. TEXT -> VARCHAR,
-- REAL -> DOUBLE PRECISION, JSON as needed) for your target engine; as
-- written this is valid SQLite / close-to-valid Postgres.
-- =============================================================================

CREATE TABLE vehicles (
    id                  TEXT PRIMARY KEY,
    callsign            TEXT UNIQUE NOT NULL,          -- e.g. 'AMB-01'
    vehicle_type        TEXT NOT NULL,                  -- ambulance | fire | police
    status              TEXT NOT NULL DEFAULT 'idle',   -- idle | enroute | off_route | at_incident | completed
    lat                 REAL NOT NULL,
    lng                 REAL NOT NULL,
    heading_deg         REAL DEFAULT 0,
    speed_kmh           REAL DEFAULT 45,
    base_speed_kmh      REAL DEFAULT 45,
    current_mission_id  TEXT REFERENCES missions(id),
    active_route_id     TEXT REFERENCES route_options(id),
    route_progress      REAL DEFAULT 0,                 -- 0..1 fraction along active route
    off_route           BOOLEAN DEFAULT 0,
    deviation_m         REAL DEFAULT 0,
    created_at          REAL
);

CREATE TABLE missions (
    id            TEXT PRIMARY KEY,
    vehicle_id    TEXT NOT NULL REFERENCES vehicles(id),
    mission_type  TEXT DEFAULT 'emergency_response',
    origin_lat    REAL NOT NULL,
    origin_lng    REAL NOT NULL,
    dest_lat      REAL NOT NULL,
    dest_lng      REAL NOT NULL,
    status        TEXT DEFAULT 'active',                -- active | completed | cancelled
    created_at    REAL
);

CREATE TABLE route_options (
    id            TEXT PRIMARY KEY,
    mission_id    TEXT NOT NULL REFERENCES missions(id),
    label         TEXT NOT NULL,                        -- A | B | C | RECOVERY
    polyline      JSON NOT NULL,                         -- [[lat,lng], ...]
    distance_km   REAL NOT NULL,
    base_eta_min  REAL NOT NULL,
    delay_min     REAL DEFAULT 0,                        -- extra delay injected by incidents
    risk_score    REAL DEFAULT 0,                         -- 0..1
    status        TEXT DEFAULT 'available',              -- available | active | blocked
    is_mock       BOOLEAN DEFAULT 1                       -- false once wired to a real routing API
);

CREATE TABLE incidents (
    id                 TEXT PRIMARY KEY,
    mission_id         TEXT REFERENCES missions(id),
    incident_type      TEXT NOT NULL,   -- accident | traffic | roadblock | flood | hazard | route_deviation | mass_casualty
    lat                REAL,
    lng                REAL,
    radius_m           REAL DEFAULT 250,
    severity           TEXT DEFAULT 'high',              -- low | medium | high
    status             TEXT DEFAULT 'active',             -- active | resolved
    affected_route_id  TEXT REFERENCES route_options(id),
    created_at         REAL
);

CREATE TABLE recommendations (
    id                     TEXT PRIMARY KEY,
    mission_id             TEXT NOT NULL REFERENCES missions(id),
    incident_id            TEXT REFERENCES incidents(id),
    recommended_route_id   TEXT REFERENCES route_options(id),
    action                 TEXT NOT NULL,    -- reroute | hold | dispatch_backup | recovery_route
    reason                 TEXT NOT NULL,    -- natural-language explanation
    confidence             REAL DEFAULT 0.7,
    extra                  JSON,              -- action-specific structured data (e.g. backup vehicle id)
    status                 TEXT DEFAULT 'pending', -- pending | applied | rejected
    created_at             REAL
);

CREATE TABLE timeline_events (
    id          TEXT PRIMARY KEY,
    mission_id  TEXT REFERENCES missions(id),
    vehicle_id  TEXT REFERENCES vehicles(id),
    event_type  TEXT NOT NULL,   -- system | dispatch | incident | reroute | deviation | hold | arrival
    message     TEXT NOT NULL,
    timestamp   REAL
);

CREATE INDEX idx_routes_mission        ON route_options(mission_id);
CREATE INDEX idx_incidents_mission     ON incidents(mission_id);
CREATE INDEX idx_recommendations_mission ON recommendations(mission_id);
CREATE INDEX idx_timeline_mission      ON timeline_events(mission_id);
CREATE INDEX idx_timeline_timestamp    ON timeline_events(timestamp);
