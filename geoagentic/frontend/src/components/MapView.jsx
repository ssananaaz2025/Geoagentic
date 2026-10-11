import React, { useMemo, useState, useEffect, useRef } from "react";
import { MapContainer, TileLayer, Marker, Polyline, Circle, Popup, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { api, BASE } from "../api";

const VEHICLE_ICONS = { ambulance: "🚑", fire: "🚒", police: "🚓" };

// Bengaluru urban bounding box (mirrors backend config.BENGALURU_BOUNDS) —
// this app is scoped to Bengaluru only, so the map is locked to this area:
// it can't be panned/zoomed away to show anywhere else.
const BLR_BOUNDS = [
  [12.83, 77.45],
  [13.14, 77.75],
];
const BLR_CENTER = [12.9716, 77.5946];

function vehicleDivIcon(vehicle, isSelected) {
  const emoji = VEHICLE_ICONS[vehicle.vehicle_type] || "🚗";
  const ring = vehicle.off_route ? "#dc2626" : isSelected ? "#f97316" : "#d6cbbd";
  return L.divIcon({
    className: "",
    html: `
      <div style="display:flex;flex-direction:column;align-items:center;transform:translateY(-6px);">
        <div style="
          width:30px;height:30px;border-radius:50%;
          background:#ffffff;border:2px solid ${ring};
          display:flex;align-items:center;justify-content:center;
          font-size:15px; box-shadow:0 1px 4px rgba(0,0,0,0.25);
        ">${emoji}</div>
        <div class="veh-icon-label" style="margin-top:2px;">${vehicle.callsign}</div>
      </div>`,
    iconSize: [30, 44],
    iconAnchor: [15, 30],
  });
}

const INCIDENT_STYLE = {
  accident: { color: "#dc2626", emoji: "💥" },
  traffic: { color: "#f97316", emoji: "🚦" },
  roadblock: { color: "#f97316", emoji: "🚧" },
  flood: { color: "#2563eb", emoji: "🌊" },
  hazard: { color: "#dc2626", emoji: "🔥" },
  mass_casualty: { color: "#f97316", emoji: "🏥" },
};

function incidentDivIcon(type) {
  const s = INCIDENT_STYLE[type] || { color: "#f97316", emoji: "⚠️" };
  return L.divIcon({
    className: "",
    html: `<div style="font-size:20px; filter:drop-shadow(0 1px 2px rgba(0,0,0,0.35));">${s.emoji}</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
}

function routeColor(route, recommendedRouteId) {
  if (route.status === "blocked") return "#dc2626";
  if (route.id === recommendedRouteId) return "#22c55e";
  if (route.traffic_status === "affected") return "#dc2626";
  if (route.status === "active") return "#ea580c";
  if (route.label === "RECOVERY") return "#9a3412";
  return "#94a3b8";
}

function FitBounds({ points }) {
  const map = useMap();
  React.useEffect(() => {
    if (points && points.length > 0) {
      const bounds = L.latLngBounds(points);
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 15 });
    }
  }, [JSON.stringify(points)]);
  return null;
}


// Small layer-toggle box. (Place search now lives in the Dispatch panel.)
// Every call goes through the backend, which holds the TomTom credentials -
// no key ever reaches the browser.
function MapOverlay({ flowOn, setFlowOn, liveOn, setLiveOn, tomtom, liveCount }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) {
      L.DomEvent.disableClickPropagation(ref.current);
      L.DomEvent.disableScrollPropagation(ref.current);
    }
  }, []);
  if (!tomtom) return null;   // nothing to toggle without a TomTom key
  return (
    <div ref={ref} className="map-overlay">
      <label className="map-toggle">
        <input type="checkbox" checked={flowOn} onChange={(e) => setFlowOn(e.target.checked)} />
        Traffic colours
      </label>
      <label className="map-toggle">
        <input type="checkbox" checked={liveOn} onChange={(e) => setLiveOn(e.target.checked)} />
        Live incidents{liveOn ? ` (${liveCount})` : ""}
      </label>
    </div>
  );
}

// Click-to-pick: while pickMode is "origin"/"dest" the next map click sets it.
function ClickToPick({ pickMode, onPick }) {
  const map = useMap();
  useEffect(() => {
    map.getContainer().style.cursor = pickMode ? "crosshair" : "";
    return () => { map.getContainer().style.cursor = ""; };
  }, [pickMode, map]);
  useMapEvents({
    click(e) {
      if (pickMode && onPick) onPick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

// Bring the chosen From / To into view as soon as they are set.
function FlyToDraft({ draft }) {
  const map = useMap();
  const o = draft?.origin, d = draft?.dest;
  const key = `${o?.auto ? "auto" : `${o?.lat},${o?.lng}`}|${d?.lat},${d?.lng}`;   // auto-origin follows the vehicle: don't re-fly each tick
  useEffect(() => {
    if (o && d) map.fitBounds(L.latLngBounds([[o.lat, o.lng], [d.lat, d.lng]]), { padding: [90, 90], maxZoom: 15 });
    else if (d) map.flyTo([d.lat, d.lng], Math.max(map.getZoom(), 14));
    else if (o && !o.auto) map.flyTo([o.lat, o.lng], Math.max(map.getZoom(), 14));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}

const pinIcon = (letter, color) => L.divIcon({
  className: "",
  html: `<div class="pin" style="background:${color}"><span>${letter}</span></div>`,
  iconSize: [30, 38],
  iconAnchor: [15, 36],
});

export default function MapView({
  vehicles, missions, incidents, recommendation, selectedVehicleId, onSelectVehicle,
  pickMode = null, onPick = null, onCancelPick = null, draft = null,
}) {
  const [tomtom, setTomtom] = useState(false);
  const [flowOn, setFlowOn] = useState(true);
  const [liveOn, setLiveOn] = useState(true);
  const liveInc = incidents.filter((incident) => incident.source === "tomtom");
  const recommendedRouteId = recommendation?.extra?.route_b?.id;

  // Ask the backend which keys are live; pick tile source accordingly.
  useEffect(() => {
    api.getConfigStatus().then((c) => setTomtom(!!c?.tomtom?.enabled)).catch(() => {});
  }, []);
  const center = useMemo(() => {
    if (vehicles.length) return [vehicles[0].lat, vehicles[0].lng];
    return BLR_CENTER;
  }, [vehicles.length]);

  const selectedMission = missions.find((m) =>
    vehicles.some((v) => v.id === selectedVehicleId && v.current_mission_id === m.id)
  );

  const boundsPoints = useMemo(() => {
    const pts = vehicles.map((v) => [v.lat, v.lng]);
    if (selectedMission) {
      selectedMission.routes.forEach((r) => r.polyline.forEach((p) => pts.push(p)));
    }
    return pts;
  }, [vehicles.length, selectedMission?.id]);

  return (
    <MapContainer
      center={center}
      zoom={13}
      minZoom={11}
      maxBounds={BLR_BOUNDS}
      maxBoundsViscosity={1.0}
      style={{ height: "100%", width: "100%" }}
      zoomControl={true}
    >
      {tomtom ? (
        <TileLayer
          key="tt-base"
          attribution="&copy; TomTom, &copy; OpenStreetMap contributors"
          url={`${BASE}/api/tiles/base/{z}/{x}/{y}.png`}
        />
      ) : (
        <TileLayer
          key="osm"
          attribution="&copy; OpenStreetMap contributors"
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
      )}
      {tomtom && flowOn && (
        <TileLayer key="tt-flow" opacity={0.75} url={`${BASE}/api/tiles/flow/{z}/{x}/{y}.png`} />
      )}
      <MapOverlay flowOn={flowOn} setFlowOn={setFlowOn} liveOn={liveOn} setLiveOn={setLiveOn}
        tomtom={tomtom} liveCount={liveInc.length} />
      <ClickToPick pickMode={pickMode} onPick={onPick} />
      <FlyToDraft draft={draft} />

      {draft?.origin && !draft.origin.auto && (
        <Marker position={[draft.origin.lat, draft.origin.lng]} icon={pinIcon("A", "#16a34a")}>
          <Popup><b>From</b><br />{draft.origin.name}</Popup>
        </Marker>
      )}
      {draft?.dest && (
        <Marker position={[draft.dest.lat, draft.dest.lng]} icon={pinIcon("B", "#dc2626")}>
          <Popup><b>To</b><br />{draft.dest.name}</Popup>
        </Marker>
      )}
      {draft?.origin && draft?.dest && (
        <Polyline
          positions={[[draft.origin.lat, draft.origin.lng], [draft.dest.lat, draft.dest.lng]]}
          pathOptions={{ color: "#64748b", weight: 2, dashArray: "4 8", opacity: 0.8 }}
        />
      )}
      {liveInc.map((inc) => (
        <Marker key={`live-${inc.id}`} position={[inc.lat, inc.lng]} icon={incidentDivIcon(inc.incident_type)}>
          <Popup>
            <b>{inc.freshness === "stale" ? "Stale TomTom" : "TomTom"} · {inc.incident_type}</b> ({inc.severity})<br />
            {inc.description || "TomTom incident"}
            {inc.source_data?.from && <><br />{inc.source_data.from}{inc.source_data.to ? ` → ${inc.source_data.to}` : ""}</>}
            {inc.delay_s ? <><br />delay ≈ {Math.round(inc.delay_s / 60)} min</> : null}
            {inc.affected_route_id && <><br />Matched to route ID {inc.affected_route_id}</>}
          </Popup>
        </Marker>
      ))}
      <FitBounds points={boundsPoints} />

      {missions.map((m) =>
        m.routes.map((r) => (
          <Polyline
            key={r.id}
            positions={r.polyline}
            pathOptions={{
              color: routeColor(r, recommendedRouteId),
              weight: r.status === "active" || r.id === recommendedRouteId ? 5 : 2,
              opacity: r.status === "blocked" ? 0.55 : r.status === "available" ? 0.75 : 0.9,
              dashArray: r.status === "blocked" ? "6 6"
                : r.traffic_status === "unverified" ? "3 6"
                  : r.label === "RECOVERY" ? "2 6" : null,
            }}
          >
            <Popup>
              Route {r.label} — {r.distance_km.toFixed(2)} km, {(r.base_eta_min + r.delay_min).toFixed(1)} min
              <br />
              status: {r.status} · risk: {r.risk_score.toFixed(2)}
              {r.traffic_status && <> · traffic: {r.traffic_status}</>}
            </Popup>
          </Polyline>
        ))
      )}

      {missions.map((m) => (
        <Marker key={`dest-${m.id}`} position={[m.dest_lat, m.dest_lng]} icon={L.divIcon({
          className: "",
          html: `<div style="font-size:18px;">🎯</div>`,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        })}>
          <Popup>Destination</Popup>
        </Marker>
      ))}

      {vehicles.map((v) => (
        <Marker
          key={v.id}
          position={[v.lat, v.lng]}
          icon={vehicleDivIcon(v, v.id === selectedVehicleId)}
          eventHandlers={{ click: () => onSelectVehicle(v.id) }}
        >
          <Popup>
            <b>{v.callsign}</b> ({v.vehicle_type})<br />
            status: {v.status}<br />
            speed: {v.speed_kmh.toFixed(0)} km/h
            {v.off_route && <><br /><span style={{ color: "#dc2626" }}>OFF ROUTE — {v.deviation_m.toFixed(0)} m</span></>}
          </Popup>
        </Marker>
      ))}

      {incidents.map((inc) =>
        inc.lat != null ? (
          <React.Fragment key={inc.id}>
            <Marker position={[inc.lat, inc.lng]} icon={incidentDivIcon(inc.incident_type)}>
              <Popup>
                {inc.incident_type} · {inc.severity}
              </Popup>
            </Marker>
            <Circle
              center={[inc.lat, inc.lng]}
              radius={inc.radius_m}
              pathOptions={{
                color: INCIDENT_STYLE[inc.incident_type]?.color || "#f97316",
                weight: 1,
                fillOpacity: 0.08,
              }}
            />
          </React.Fragment>
        ) : null
      )}

      {pickMode && (
        <div className="pick-banner">
          <span>
            Click the map to set the <b>{pickMode === "origin" ? "START" : "DESTINATION"}</b>
          </span>
          <button onClick={onCancelPick}>Cancel</button>
        </div>
      )}

      <div className="legend">
        <div className="legend-item"><span className="legend-swatch" style={{ background: "#ea580c" }} /> active route</div>
        <div className="legend-item"><span className="legend-swatch" style={{ background: "#dc2626" }} /> blocked</div>
        <div className="legend-item"><span className="legend-swatch" style={{ background: "#9a3412" }} /> recovery</div>
        <div className="legend-item"><span className="legend-swatch" style={{ background: "#94a3b8" }} /> alternative</div>
      </div>
    </MapContainer>
  );
}
