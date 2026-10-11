import React, { useState } from "react";
import { api } from "../api";

/**
 * Bottom bar: try out an incident on the selected vehicle's route and see
 * what the GeoAgent suggests. Plain names; hover for a one-line explanation.
 */
const EVENTS = [
  { type: "accident", label: "Accident", icon: "💥", tip: "A crash on the route ahead" },
  { type: "traffic", label: "Heavy traffic", icon: "🚦", tip: "Slow-moving traffic ahead" },
  { type: "roadblock", label: "Road blocked", icon: "🚧", tip: "The road ahead is closed" },
  { type: "flood", label: "Flooding", icon: "🌊", tip: "Water-logged stretch ahead" },
  { type: "hazard", label: "Hazard zone", icon: "🔥", tip: "A danger zone around the vehicle" },
  { type: "deviation", label: "Off route", icon: "📡", tip: "The vehicle drifts off its planned route" },
  { type: "mass_casualty", label: "Many casualties", icon: "🏥", tip: "The selected ambulance is on scene; stop it and request backup" },
];

// A point on the active route AHEAD of the vehicle, so the incident is
// always something it is still going to drive into.
function pointAhead(vehicle, mission) {
  const route = mission?.routes?.find((r) => r.status === "active") || mission?.routes?.[0];
  if (!route?.polyline?.length) return { lat: vehicle.lat, lng: vehicle.lng };
  const p = vehicle.route_progress || 0;
  const frac = p + (1 - p) * 0.5;
  const i = Math.min(route.polyline.length - 1, Math.max(0, Math.round(frac * (route.polyline.length - 1))));
  return { lat: route.polyline[i][0], lng: route.polyline[i][1] };
}

export default function ScenarioControls({ selectedVehicle, selectedMission, onBusyChange, onDone }) {
  const [pending, setPending] = useState(null);
  const [err, setErr] = useState("");

  async function fire(type) {
    setPending(type); setErr(""); onBusyChange?.(true);
    try {
      if (type === "deviation") {
        await api.triggerDeviation(selectedVehicle.id, 400);
      } else if (type === "hazard") {
        await api.createIncident({
          incident_type: "hazard", lat: selectedVehicle.lat, lng: selectedVehicle.lng,
          radius_m: 600, severity: "high",
        });
      } else {
        const p = type === "mass_casualty"
          ? { lat: selectedVehicle.lat, lng: selectedVehicle.lng }
          : pointAhead(selectedVehicle, selectedMission);
        await api.createIncident({
          mission_id: selectedMission.id, incident_type: type, lat: p.lat, lng: p.lng,
          radius_m: type === "flood" ? 400 : 250, severity: type === "traffic" ? "medium" : "high",
        });
      }
      await onDone?.();
    } catch (e) {
      setErr(e.message);
    } finally {
      setPending(null);
      onBusyChange?.(false);
    }
  }

  const noTrip = !selectedVehicle || !selectedMission;

  return (
    <div className="control-bar">
      <div className="control-bar-label">
        TRY AN INCIDENT
        <span>{noTrip ? "Dispatch a vehicle first" : `on ${selectedVehicle.callsign}'s route`}</span>
      </div>
      {EVENTS.map((s) => (
        <button
          key={s.type}
          className="scenario-btn"
          title={noTrip ? "Dispatch a vehicle first" : s.tip}
          disabled={noTrip || pending === s.type}
          onClick={() => fire(s.type)}
        >
          <span className="icon">{s.icon}</span>
          <span>{pending === s.type ? "Checking…" : s.label}</span>
        </button>
      ))}
      {err && <span className="bar-err">{err}</span>}
    </div>
  );
}
