import React, { useCallback, useEffect, useState } from "react";
import MapView from "../components/MapView.jsx";
import VehicleRoster from "../components/VehicleRoster.jsx";
import TripSetup from "../components/TripSetup.jsx";
import ScenarioControls from "../components/ScenarioControls.jsx";
import TripPanel from "../components/TripPanel.jsx";
import Timeline from "../components/Timeline.jsx";
import RoleSwitch from "../components/RoleSwitch.jsx";
import ConnectionBanner from "../components/ConnectionBanner.jsx";
import { api } from "../api";
import { useGeoState } from "../useGeoState";
import { useNow } from "../useNow";
import { nameForPoint, inBengaluru, loadPlaces } from "../places";

/**
 * Control Room: dispatch vehicles, watch them move, and accept/ignore the
 * GeoAgent's suggestions. Layout: [dispatch + fleet] [map] [selected vehicle].
 * The From/To being chosen live here (`draft`) so the map can show the pins
 * and let the user click to set them.
 */
export default function ControlRoomDashboard() {
  const { state, fetchState, online } = useGeoState();
  const now = useNow(1000);
  const [selectedVehicleId, setSelectedVehicleId] = useState(null);
  const [draft, setDraft] = useState({ origin: null, dest: null });
  const [pickMode, setPickMode] = useState(null);       // "origin" | "dest" | null
  const [busy, setBusy] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentTrace, setAgentTrace] = useState([]);
  const [toast, setToast] = useState("");

  useEffect(() => { loadPlaces(); }, []);

  useEffect(() => {
    setSelectedVehicleId((prev) =>
      prev && state.vehicles.some((v) => v.id === prev) ? prev : state.vehicles[0]?.id || null);
  }, [state.vehicles]);

  // Esc cancels map picking
  useEffect(() => {
    const h = (e) => { if (e.key === "Escape") setPickMode(null); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const vehicle = state.vehicles.find((v) => v.id === selectedVehicleId) || null;
  const mission = vehicle ? state.missions.find((m) => m.id === vehicle.current_mission_id) || null : null;
  // Derived from live data, so a pending suggestion survives a page refresh.
  const recommendation = mission
    ? state.recommendations.find((r) => r.mission_id === mission.id && r.status === "pending") || null
    : null;
  const trafficMonitor = mission
    ? state.traffic_monitor?.find((item) => item.mission_id === mission.id) || null
    : null;

  // When "From" is the vehicle's own position, draw it from the live position
  // (the vehicle marker already shows it, so no extra pin is drawn for it).
  const mapDraft = {
    origin: draft.origin?.auto && vehicle ? { ...draft.origin, lat: vehicle.lat, lng: vehicle.lng } : draft.origin,
    dest: draft.dest,
  };

  const handlePick = useCallback((lat, lng) => {
    if (!pickMode) return;
    if (!inBengaluru(lat, lng)) {
      setToast("That spot is outside Bengaluru — this demo only covers the city.");
      return;
    }
    const spot = { name: nameForPoint(lat, lng), lat, lng };
    setDraft((d) => ({ ...d, [pickMode]: spot }));
    setPickMode(null);
  }, [pickMode]);

  async function runAgents() {
    if (!mission) return;
    setAgentBusy(true);
    try {
      const result = await api.runAgents(mission.id);
      setAgentTrace(result.trace || []);
      await fetchState();
    } catch (e) {
      setToast(e.message);
    } finally {
      setAgentBusy(false);
    }
  }

  async function handleReset() {
    if (!confirm("Reset everything back to the demo starting state?")) return;
    try {
      await api.reset();
      setSelectedVehicleId(null);
      setDraft({ origin: null, dest: null });
      setPickMode(null);
      setAgentTrace([]);
      fetchState();
    } catch (e) {
      setToast(e.message);
    }
  }

  return (
    <div className="app-shell">
      <ConnectionBanner online={online} />
      <div className="topbar">
        <div className="brand">
          <div className="brand-mark" />
          <span className="brand-name">GEOAGENTIC</span>
          <span className="brand-sub">control room</span>
        </div>
        <RoleSwitch active="#/control-room" />
        <div className="topbar-right">
          <span className={"live-dot" + (online ? "" : " off")} title={online ? "Connected" : "Backend offline"} />
          <span className="clock">{new Date(now).toLocaleTimeString()}</span>
          <button className="reset-btn" onClick={handleReset}>Reset demo</button>
        </div>
      </div>

      <div className="main-grid">
        <div className="pane">
          <TripSetup
            vehicles={state.vehicles}
            selectedVehicleId={selectedVehicleId}
            onSelectVehicle={setSelectedVehicleId}
            draft={draft} setDraft={setDraft}
            pickMode={pickMode} setPickMode={setPickMode}
            onMissionCreated={(m, vid) => { setSelectedVehicleId(vid); fetchState(); }}
            onVehicleCreated={() => fetchState()}
          />
          <VehicleRoster
            vehicles={state.vehicles}
            missions={state.missions}
            selectedVehicleId={selectedVehicleId}
            onSelect={setSelectedVehicleId}
          />
        </div>

        <div className="pane map-pane" style={{ padding: 0 }}>
          <MapView
            vehicles={state.vehicles}
            missions={state.missions}
            incidents={state.incidents}
            recommendation={recommendation}
            selectedVehicleId={selectedVehicleId}
            onSelectVehicle={setSelectedVehicleId}
            pickMode={pickMode}
            onPick={handlePick}
            onCancelPick={() => setPickMode(null)}
            draft={mapDraft}
          />
          {toast && <div className="toast">{toast}</div>}
        </div>

        <div className="pane">
          <TripPanel
            vehicle={vehicle}
            mission={mission}
            recommendation={recommendation}
            trafficMonitor={trafficMonitor}
            busy={busy}
            agentBusy={agentBusy}
            agentTrace={agentTrace}
            onRunAgents={runAgents}
            onResolved={fetchState}
            onCheckTraffic={async () => {
              if (!mission) return;
              await api.checkMissionTraffic(mission.id);
              await fetchState();
            }}
          />
          <div className="section-divider" style={{ paddingTop: 0, marginTop: 0 }}>
            <Timeline events={state.timeline} />
          </div>
        </div>
      </div>

      <ScenarioControls
        selectedVehicle={vehicle}
        selectedMission={mission}
        onBusyChange={setBusy}
        onDone={fetchState}
      />
    </div>
  );
}
