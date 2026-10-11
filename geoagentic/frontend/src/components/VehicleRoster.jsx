import React from "react";
import { useNow } from "../useNow";
import { remainingMin, liveProgress, fmtDuration } from "../eta";

const TYPE_ICON = { ambulance: "🚑", fire: "🚒", police: "🚓" };
const STATUS_LABEL = {
  idle: "Free", enroute: "En route", off_route: "Off route", at_incident: "At incident", completed: "Arrived",
};

/** Compact fleet list. Click a vehicle to select it everywhere. */
export default function VehicleRoster({ vehicles, missions, selectedVehicleId, onSelect }) {
  useNow(1000);
  return (
    <div>
      <div className="pane-header">FLEET ({vehicles.length})</div>
      {vehicles.length === 0 && <div className="empty-state">No vehicles yet.</div>}
      {vehicles.map((v) => {
        const mission = missions.find((m) => m.id === v.current_mission_id);
        const route = mission?.routes?.find((r) => r.id === v.active_route_id) || mission?.routes?.find((r) => r.status === "active");
        const moving = route && (v.status === "enroute" || v.status === "off_route");
        return (
          <div
            key={v.id}
            className={`vehicle-card ${v.id === selectedVehicleId ? "selected" : ""}`}
            onClick={() => onSelect(v.id)}
          >
            <div className="vehicle-row-top">
              <span className="vehicle-callsign">
                <span className="vehicle-type-icon">{TYPE_ICON[v.vehicle_type] || "🚗"}</span>{v.callsign}
              </span>
              <span className={`status-pill status-${v.status}`}>{STATUS_LABEL[v.status] || v.status}</span>
            </div>
            {moving && (
              <>
                <div className="mini-progress"><div style={{ width: `${Math.round(liveProgress(v, route) * 100)}%` }} /></div>
                <div className="vehicle-meta">
                  <span>{fmtDuration(remainingMin(v, route))} left</span>
                  <span>{v.speed_kmh.toFixed(0)} km/h</span>
                </div>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
