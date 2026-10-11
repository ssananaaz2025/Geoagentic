import React, { useEffect, useState } from "react";
import RoleSwitch from "../components/RoleSwitch.jsx";
import Timeline from "../components/Timeline.jsx";
import ConnectionBanner from "../components/ConnectionBanner.jsx";
import LocationPicker from "../components/LocationPicker.jsx";
import { api } from "../api";
import { useGeoState } from "../useGeoState";
import { useNow } from "../useNow";

const NEW_VEHICLE_DEFAULTS = { callsign: "", vehicle_type: "ambulance" };

function StatusDot({ ok }) {
  return <span className={`status-dot ${ok ? "on" : "off"}`} />;
}

/**
 * Admin dashboard — fleet management and system monitoring. Not tied to
 * any single vehicle or mission: shows the whole fleet, whether the two
 * real integrations (Google Maps Directions, Claude LLM) are live, lets
 * an operator add a vehicle to the roster, and exposes the demo reset.
 */
export default function AdminDashboard() {
  const { state, fetchState, online } = useGeoState();
  const now = useNow(1000);
  const [integrations, setIntegrations] = useState(null);
  const [form, setForm] = useState(NEW_VEHICLE_DEFAULTS);
  const [loc, setLoc] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getConfigStatus().then(setIntegrations).catch((e) => console.error("config status failed", e));
  }, []);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function addVehicle() {
    setBusy(true);
    setError("");
    try {
      if (!form.callsign.trim()) throw new Error("Enter a callsign");
      if (!loc) throw new Error("Choose where the vehicle starts");
      await api.createVehicle({
        callsign: form.callsign.trim(), vehicle_type: form.vehicle_type, lat: loc.lat, lng: loc.lng,
      });
      setForm(NEW_VEHICLE_DEFAULTS);
      setLoc(null);
      fetchState();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    if (!confirm("Reset all vehicles, missions and incidents to the demo starting state?")) return;
    try {
      await api.reset();
      fetchState();
    } catch (e) {
      setError(e.message);
    }
  }

  const byStatus = {};
  const byType = {};
  for (const v of state.vehicles) {
    byStatus[v.status] = (byStatus[v.status] || 0) + 1;
    byType[v.vehicle_type] = (byType[v.vehicle_type] || 0) + 1;
  }
  const pendingRecs = state.recommendations.filter((r) => r.status === "pending").length;
  const appliedRecs = state.recommendations.filter((r) => r.status === "applied").length;

  return (
    <div className="app-shell admin-shell">
      <ConnectionBanner online={online} />
      <div className="topbar">
        <div className="brand">
          <div className="brand-mark" />
          <span className="brand-name">GEOAGENTIC</span>
          <span className="brand-sub">admin — fleet &amp; system</span>
        </div>
        <RoleSwitch active="#/admin" />
        <div className="topbar-right">
          <span className={"live-dot" + (online ? "" : " off")} title={online ? "Connected" : "Backend offline"} />
          <span className="clock">{new Date(now).toLocaleTimeString()}</span>
          <button className="reset-btn" onClick={handleReset}>Reset demo</button>
        </div>
      </div>

      <div className="admin-scroll">
        <div className="admin-grid">
          <div className="admin-col">
            <div className="pane-header" style={{ padding: "6px 0" }}>SYSTEM INTEGRATIONS</div>
            {!integrations ? (
              <div className="empty-state">Loading…</div>
            ) : (
              <div className="stat-grid">
                <div className="stat-card">
                  <div className="stat-card-top">
                    <StatusDot ok={integrations.google_maps.enabled} />
                    <span className="stat-card-title">Google Maps Directions</span>
                  </div>
                  <div className="stat-card-value">{integrations.google_maps.enabled ? "LIVE" : "MOCK ROUTES"}</div>
                  <div className="stat-card-sub">region: {integrations.google_maps.region}</div>
                </div>
                <div className="stat-card">
                  <div className="stat-card-top">
                    <StatusDot ok={integrations.routing?.osrm_enabled} />
                    <span className="stat-card-title">OSRM (OpenStreetMap routing)</span>
                  </div>
                  <div className="stat-card-value">{integrations.routing?.osrm_enabled ? "ENABLED" : "OFF"}</div>
                  <div className="stat-card-sub">mode: {integrations.routing?.provider_mode} · free-flow ETA, no live traffic</div>
                </div>
                <div className="stat-card">
                  <div className="stat-card-top">
                    <StatusDot ok={integrations.langgraph?.enabled} />
                    <span className="stat-card-title">LangGraph agent team</span>
                  </div>
                  <div className="stat-card-value">{integrations.langgraph?.enabled ? "READY" : "NOT INSTALLED"}</div>
                  <div className="stat-card-sub">supervisor + 5 specialists, human approval</div>
                </div>
                <div className="stat-card">
                  <div className="stat-card-top">
                    <StatusDot ok={integrations.claude_llm.enabled} />
                    <span className="stat-card-title">Claude LLM explanations</span>
                  </div>
                  <div className="stat-card-value">{integrations.claude_llm.enabled ? "LIVE" : "TEMPLATED"}</div>
                  <div className="stat-card-sub">{integrations.claude_llm.model || "no model — using fallback"}</div>
                </div>
                <div className="stat-card">
                  <div className="stat-card-top">
                    <StatusDot ok={true} />
                    <span className="stat-card-title">Database</span>
                  </div>
                  <div className="stat-card-value">{integrations.database_url_scheme.toUpperCase()}</div>
                  <div className="stat-card-sub">persistence layer</div>
                </div>
              </div>
            )}
            <div className="hint-text">
              Both integrations degrade gracefully: without an API key, routing falls back to mock
              curved routes and explanations fall back to a templated sentence — the app never breaks.
            </div>

            <div className="pane-header" style={{ padding: "16px 0 6px" }}>FLEET SUMMARY</div>
            <div className="stat-grid">
              <div className="stat-card">
                <div className="stat-card-title">Total vehicles</div>
                <div className="stat-card-value">{state.vehicles.length}</div>
              </div>
              <div className="stat-card">
                <div className="stat-card-title">Active missions</div>
                <div className="stat-card-value">{state.missions.filter((m) => m.status !== "completed").length}</div>
              </div>
              <div className="stat-card">
                <div className="stat-card-title">Pending recs</div>
                <div className="stat-card-value">{pendingRecs}</div>
              </div>
              <div className="stat-card">
                <div className="stat-card-title">Applied recs</div>
                <div className="stat-card-value">{appliedRecs}</div>
              </div>
            </div>
            <div className="stat-grid" style={{ marginTop: 8 }}>
              {Object.entries(byType).map(([t, n]) => (
                <div className="stat-card" key={t}>
                  <div className="stat-card-title">{t}</div>
                  <div className="stat-card-value">{n}</div>
                </div>
              ))}
            </div>

            <div className="pane-header" style={{ padding: "16px 0 6px" }}>ADD VEHICLE TO FLEET</div>
            <div className="field-pair" style={{ padding: 0 }}>
              <div>
                <span className="field-label">Callsign</span>
                <input placeholder="AMB-03" value={form.callsign} onChange={set("callsign")} />
              </div>
              <div>
                <span className="field-label">Type</span>
                <select value={form.vehicle_type} onChange={set("vehicle_type")}>
                  <option value="ambulance">Ambulance</option>
                  <option value="fire">Fire engine</option>
                  <option value="police">Police</option>
                </select>
              </div>
            </div>
            <div style={{ marginTop: 10 }}>
              <LocationPicker
                label="Starts at" color="#16a34a" placeholder="Search a place or area…"
                value={loc} onChange={setLoc} picking={false} onTogglePick={null}
              />
            </div>
            {error && <div className="hint-text" style={{ color: "var(--red)", padding: "8px 0 0" }}>{error}</div>}
            <button className="primary-btn" style={{ margin: "12px 0", width: "100%" }} onClick={addVehicle} disabled={busy}>
              {busy ? "Adding…" : "Add vehicle"}
            </button>
            <div className="hint-text" style={{ padding: "0 0 8px" }}>
              Bengaluru urban area only.
            </div>
          </div>

          <div className="admin-col admin-col-wide">
            <div className="pane-header" style={{ padding: "6px 0" }}>FLEET ROSTER</div>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Callsign</th><th>Type</th><th>Status</th><th>Speed</th><th>Position</th><th>Mission</th><th>Deviation</th>
                  </tr>
                </thead>
                <tbody>
                  {state.vehicles.map((v) => (
                    <tr key={v.id} className={v.off_route ? "row-alert" : ""}>
                      <td>{v.callsign}</td>
                      <td>{v.vehicle_type}</td>
                      <td>{v.status}</td>
                      <td>{v.speed_kmh.toFixed(0)} km/h</td>
                      <td>{v.lat.toFixed(4)}, {v.lng.toFixed(4)}</td>
                      <td>{v.current_mission_id || "—"}</td>
                      <td>{v.off_route ? `${v.deviation_m.toFixed(0)} m` : "—"}</td>
                    </tr>
                  ))}
                  {state.vehicles.length === 0 && (
                    <tr><td colSpan={7} className="empty-state">No vehicles yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="pane-header" style={{ padding: "16px 0 6px" }}>MISSIONS</div>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Vehicle</th><th>Status</th><th>Origin</th><th>Destination</th><th>Routes</th>
                  </tr>
                </thead>
                <tbody>
                  {state.missions.map((m) => {
                    const veh = state.vehicles.find((v) => v.id === m.vehicle_id);
                    const realRoutes = m.routes.filter((r) => !r.is_mock).length;
                    return (
                      <tr key={m.id}>
                        <td>{veh?.callsign || m.vehicle_id}</td>
                        <td>{m.status}</td>
                        <td>{m.origin_lat.toFixed(4)}, {m.origin_lng.toFixed(4)}</td>
                        <td>{m.dest_lat.toFixed(4)}, {m.dest_lng.toFixed(4)}</td>
                        <td>{m.routes.length} ({realRoutes ? `${realRoutes} live` : "mock"})</td>
                      </tr>
                    );
                  })}
                  {state.missions.length === 0 && (
                    <tr><td colSpan={5} className="empty-state">No missions yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="pane-header" style={{ padding: "16px 0 6px" }}>GLOBAL TIMELINE</div>
            <div className="admin-timeline-wrap">
              <Timeline events={state.timeline} limit={40} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
