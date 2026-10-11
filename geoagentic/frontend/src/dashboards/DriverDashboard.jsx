import React, { useEffect, useState } from "react";
import MapView from "../components/MapView.jsx";
import Timeline from "../components/Timeline.jsx";
import RoleSwitch from "../components/RoleSwitch.jsx";
import ConnectionBanner from "../components/ConnectionBanner.jsx";
import { api } from "../api";
import { useGeoState } from "../useGeoState";
import { useNow } from "../useNow";
import { remainingMin, fmtDuration, arrivalClock } from "../eta";
import { loadPlaces, nameForPoint } from "../places";

const TYPE_ICON = { ambulance: "🚑", fire: "🚒", police: "🚓" };
const STATUS_LABEL = {
  idle: "IDLE",
  enroute: "EN ROUTE",
  off_route: "OFF ROUTE",
  at_incident: "AT INCIDENT",
  completed: "ARRIVED",
};
const ACTION_LABEL = {
  reroute: "REROUTE",
  recovery_route: "RECOVERY ROUTE",
  dispatch_backup: "DISPATCH BACKUP",
  hold: "HOLD ROUTE",
};

function etaClass(delay) {
  if (delay > 10) return "crit";
  if (delay > 0) return "warn";
  return "ok";
}

/**
 * Driver dashboard — a focused, single-vehicle, in-vehicle view. There's no
 * real auth in this demo, so the driver picks "their" vehicle from a
 * dropdown; in production this would come from the logged-in device.
 *
 * Deliberately read-only for recommendations: the driver sees a pending
 * GeoAgent recommendation but can't Apply/Dismiss it — that decision stays
 * with the Control Room, matching a real chain of command. The one action
 * this dashboard exposes is "Report GPS deviation", standing in for the
 * vehicle's own telemetry reporting it has drifted off its planned route
 * (scenario 6), which the control room then sees and can act on.
 */
export default function DriverDashboard() {
  const { state, fetchState, online } = useGeoState();
  const now = useNow(1000);
  const [myVehicleId, setMyVehicleId] = useState(null);
  const [reporting, setReporting] = useState(false);
  const [reportErr, setReportErr] = useState("");

  useEffect(() => { loadPlaces(); }, []);

  useEffect(() => {
    setMyVehicleId((prev) => (prev && state.vehicles.some((v) => v.id === prev)) ? prev : (state.vehicles[0]?.id || null));
  }, [state.vehicles]);

  const vehicle = state.vehicles.find((v) => v.id === myVehicleId) || null;
  const mission = vehicle ? state.missions.find((m) => m.id === vehicle.current_mission_id) : null;
  const activeRoute = mission?.routes?.find((r) => r.id === vehicle?.active_route_id)
    || mission?.routes?.find((r) => r.status === "active");
  const pendingRec = mission
    ? state.recommendations.find((r) => r.mission_id === mission.id && r.status === "pending")
    : null;
  const myTimeline = vehicle ? state.timeline.filter((e) => e.vehicle_id === vehicle.id) : [];

  async function reportDeviation() {
    if (!vehicle) return;
    setReporting(true);
    setReportErr("");
    try {
      await api.triggerDeviation(vehicle.id, 400);
      await fetchState();
    } catch (e) {
      setReportErr(e.message);
    } finally {
      setReporting(false);
    }
  }

  return (
    <div className="app-shell driver-shell">
      <ConnectionBanner online={online} />
      <div className="topbar">
        <div className="brand">
          <div className="brand-mark" />
          <span className="brand-name">GEOAGENTIC</span>
          <span className="brand-sub">driver</span>
        </div>
        <RoleSwitch active="#/driver" />
        <div className="topbar-right">
          <span className="clock">{new Date(now).toLocaleTimeString()}</span>
          <select
            className="driver-vehicle-select"
            value={myVehicleId || ""}
            onChange={(e) => setMyVehicleId(e.target.value)}
          >
            {state.vehicles.length === 0 && <option value="">No vehicles</option>}
            {state.vehicles.map((v) => (
              <option key={v.id} value={v.id}>
                {v.callsign} ({v.vehicle_type})
              </option>
            ))}
          </select>
        </div>
      </div>

      {!vehicle ? (
        <div className="empty-state" style={{ padding: 40 }}>
          No vehicles yet — dispatch one from the Control Room first.
        </div>
      ) : (
        <div className="driver-grid">
          <div className="pane driver-info-pane">
            <div className="pane-header">MY VEHICLE</div>
            <div className="driver-status-card">
              <div className="driver-status-top">
                <span className="driver-callsign">
                  <span className="vehicle-type-icon">{TYPE_ICON[vehicle.vehicle_type] || "🚗"}</span>
                  {vehicle.callsign}
                </span>
                <span className={`status-pill status-${vehicle.status}`}>
                  {STATUS_LABEL[vehicle.status] || vehicle.status}
                </span>
              </div>
              <div className="vehicle-meta" style={{ marginTop: 8 }}>
                <span>{vehicle.speed_kmh.toFixed(0)} km/h</span>
                <span>{vehicle.lat.toFixed(4)}, {vehicle.lng.toFixed(4)}</span>
              </div>
              {vehicle.off_route && (
                <div className="driver-alert">
                  ⚠ OFF ROUTE — {vehicle.deviation_m.toFixed(0)} m from planned corridor
                </div>
              )}
            </div>

            {mission ? (
              <>
                <div className="pane-header" style={{ marginTop: 6 }}>CURRENT MISSION</div>
                <div className="driver-mission-card">
                  <div className="vehicle-meta">
                    <span>Origin</span>
                    <span>{nameForPoint(mission.origin_lat, mission.origin_lng)}</span>
                  </div>
                  <div className="vehicle-meta" style={{ marginTop: 4 }}>
                    <span>Destination</span>
                    <span>{nameForPoint(mission.dest_lat, mission.dest_lng)}</span>
                  </div>
                </div>

                {activeRoute && (
                  <div className="eta-cards">
                    <div className="eta-card">
                      <div className="label">Arrives in</div>
                      <div className={`value ${etaClass(activeRoute.delay_min)}`}>
                        {fmtDuration(remainingMin(vehicle, activeRoute))}
                      </div>
                      <div className="label" style={{ marginTop: 2 }}>
                        ~{arrivalClock(remainingMin(vehicle, activeRoute))}
                      </div>
                    </div>
                    <div className="eta-card">
                      <div className="label">Route</div>
                      <div className="value" style={{ color: "var(--cyan)" }}>
                        {activeRoute.label}
                        {!activeRoute.is_mock && <span className="live-badge">LIVE</span>}
                      </div>
                    </div>
                    <div className="eta-card">
                      <div className="label">Distance</div>
                      <div className="value">{activeRoute.distance_km.toFixed(2)}
                        <span style={{ fontSize: 11, marginLeft: 3 }}>km</span>
                      </div>
                    </div>
                    <div className="eta-card">
                      <div className="label">Delay</div>
                      <div className={`value ${etaClass(activeRoute.delay_min)}`}>
                        +{activeRoute.delay_min.toFixed(1)}
                        <span style={{ fontSize: 11, marginLeft: 3 }}>min</span>
                      </div>
                    </div>
                  </div>
                )}

                {pendingRec ? (
                  <div className="rec-card driver-rec-card">
                    <div className="rec-action">
                      {ACTION_LABEL[pendingRec.action] || pendingRec.action} — pending dispatcher approval
                    </div>
                    <div className="rec-reason">{pendingRec.reason}</div>
                    <div className="confidence-row">
                      <div className="confidence-track">
                        <div className="confidence-fill" style={{ width: `${Math.round(pendingRec.confidence * 100)}%` }} />
                      </div>
                      <span className="confidence-label">{Math.round(pendingRec.confidence * 100)}%</span>
                    </div>
                    <div className="hint-text" style={{ padding: 0 }}>
                      GeoAgent recommendation awaiting Control Room approval — this dashboard is read-only.
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">No pending recommendation for this mission.</div>
                )}

                <div className="section-divider">
                  <button className="primary-btn deviation-btn" onClick={reportDeviation} disabled={reporting}>
                    {reporting ? "Reporting…" : "📡 Report GPS deviation"}
                  </button>
                  {reportErr && <div className="note err">{reportErr}</div>}
                  <div className="hint-text">
                    Simulates this vehicle's telemetry drifting off the planned route (scenario 6) —
                    stands in for a real GPS/ESP32 feed. GeoAgent will detect it and offer a recovery route.
                  </div>
                </div>
              </>
            ) : (
              <div className="empty-state">No active mission — waiting for dispatch.</div>
            )}
          </div>

          <div className="pane map-pane driver-map-pane" style={{ padding: 0 }}>
            <MapView
              vehicles={[vehicle]}
              missions={mission ? [mission] : []}
              incidents={state.incidents}
              selectedVehicleId={vehicle.id}
              onSelectVehicle={() => {}}
            />
          </div>

          <div className="pane">
            <Timeline events={myTimeline} />
          </div>
        </div>
      )}
    </div>
  );
}
