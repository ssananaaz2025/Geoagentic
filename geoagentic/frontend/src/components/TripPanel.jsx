import React, { useState } from "react";
import { api } from "../api";
import { useNow } from "../useNow";
import { remainingMin, liveProgress, kmLeft, fmtDuration, arrivalClock, riskLabel } from "../eta";

const TYPE_ICON = { ambulance: "🚑", fire: "🚒", police: "🚓" };
const STATUS_LABEL = {
  idle: "Free", enroute: "En route", off_route: "Off route", at_incident: "At incident", completed: "Arrived",
};
export const ACTION_LABEL = {
  reroute: "Switch to a better route",
  recovery_route: "Get back on route",
  dispatch_backup: "Send a backup vehicle",
  hold: "Keep the current route",
};
const EXPLANATION_LABELS = {
  "What happened?": "Incident detected",
  "Why does it matter?": "Impact on the journey",
  "What do we recommend?": "Recommended route",
  "Why is this option better?": "Expected time difference",
  "What should the dispatcher do?": "Dispatcher action",
};

function explanationRows(reason = "") {
  return reason.split("\n").map((line) => {
    const match = Object.keys(EXPLANATION_LABELS).find((key) => line.startsWith(key));
    if (!match) return { label: "", text: line };
    return {
      label: EXPLANATION_LABELS[match],
      text: line.slice(match.length).trim(),
    };
  }).filter((row) => row.text);
}

function trafficStatusLabel(status) {
  if (status === "live") return "Checking traffic · Live data";
  if (status === "stale" || status === "unavailable") return "Traffic data unavailable";
  if (status === "pending") return "Checking traffic";
  return "Traffic check complete";
}

/**
 * Right-hand panel for the selected vehicle: live countdown, progress, and
 * the GeoAgent suggestion (Apply / Dismiss). The countdown is recomputed
 * every second from the last known position + speed, so it visibly ticks.
 */
export default function TripPanel({
  vehicle, mission, recommendation, trafficMonitor, busy, agentBusy, agentTrace, onRunAgents,
  onResolved, onCheckTraffic,
}) {
  useNow(1000);
  const [working, setWorking] = useState(false);
  const [err, setErr] = useState("");

  // the route the vehicle is on - still shown (flagged) even if an incident just blocked it
  const route = mission?.routes?.find((r) => r.id === vehicle?.active_route_id)
    || mission?.routes?.find((r) => r.status === "active");
  const left = remainingMin(vehicle, route);
  const pct = Math.round(liveProgress(vehicle, route) * 100);
  const risk = route ? riskLabel(route.risk_score) : null;

  async function decide(kind) {
    if (!recommendation) return;
    setWorking(true); setErr("");
    try {
      await (kind === "apply" ? api.applyRecommendation(recommendation.id) : api.rejectRecommendation(recommendation.id));
      await onResolved?.(kind);
    } catch (e) {
      setErr(e.message);
    } finally {
      setWorking(false);
    }
  }

  if (!vehicle) {
    return (
      <div className="trip-panel">
        <div className="pane-header">SELECTED VEHICLE</div>
        <div className="empty-state">Pick a vehicle on the left or on the map.</div>
      </div>
    );
  }

  return (
    <div className="trip-panel">
      <div className="pane-header">SELECTED VEHICLE</div>
      <div className="tp-head">
        <span className="tp-name">{TYPE_ICON[vehicle.vehicle_type] || "🚗"} {vehicle.callsign}</span>
        <span className={`status-pill status-${vehicle.status}`}>{STATUS_LABEL[vehicle.status] || vehicle.status}</span>
      </div>

      {vehicle.status === "completed" && (
        <div className="tp-arrived">✅ Arrived at the destination</div>
      )}

      {route && vehicle.status !== "completed" && (
        <div className="tp-eta">
          <div className="tp-eta-label">{vehicle.status === "at_incident" ? "Scene status" : "Arrives in"}</div>
          <div className="tp-eta-value">
            {vehicle.status === "at_incident" ? "On scene" : fmtDuration(left)}
          </div>
          <div className="tp-eta-sub">
            {vehicle.status === "at_incident"
              ? "Stopped at the incident location"
              : `at about ${arrivalClock(left)} · ${kmLeft(vehicle, route).toFixed(1)} km to go`}
          </div>
          <div className="tp-progress"><div className="tp-progress-fill" style={{ width: `${pct}%` }} /></div>
          <div className="tp-facts">
            <span>Route <b>{route.label}</b>{route.status === "blocked" && <b className="crit"> blocked</b>}{!route.is_mock && <span className="live-badge">LIVE</span>}</span>
            <span>Delay <b className={route.delay_min > 0 ? "warn" : "ok"}>{route.delay_min > 0 ? `+${route.delay_min.toFixed(0)} min` : "none"}</b></span>
            <span>Risk <b className={risk.cls}>{risk.text}</b></span>
          </div>
        </div>
      )}

      {!mission && vehicle.status !== "completed" && (
        <div className="empty-state">No trip yet. Use <b>Dispatch</b> on the left to send this vehicle somewhere.</div>
      )}

      {vehicle.off_route && (
        <div className="note err" style={{ margin: "0 14px 10px" }}>
          Off route by {vehicle.deviation_m.toFixed(0)} m
        </div>
      )}

      <div className="pane-header" style={{ paddingTop: 6 }}>
        GEOAGENT — {busy ? "ANALYSING…" : recommendation ? "SUGGESTION READY" : "WATCHING THE ROUTE"}
      </div>

      {mission && (
        <div className={`traffic-monitor-state ${trafficMonitor?.status || "pending"}`}>
          <span>
            {trafficStatusLabel(trafficMonitor?.status || "pending")}
            {trafficMonitor?.last_checked_at
              ? ` · checked ${new Date(trafficMonitor.last_checked_at * 1000).toLocaleTimeString()}`
              : ""}
            {trafficMonitor?.last_error && (trafficMonitor?.status === "stale" || trafficMonitor?.status === "unavailable")
              ? ` · ${trafficMonitor.last_error.replaceAll("_", " ")}`
              : ""}
          </span>
          <button
            type="button"
            onClick={async () => {
              setErr("");
              try { await onCheckTraffic?.(); } catch (e) { setErr(e.message); }
            }}
            disabled={!onCheckTraffic || trafficMonitor?.last_error === "TomTom API key is not configured"}
          >
            Check now
          </button>
        </div>
      )}

      {recommendation ? (
        <div className="rec-card">
          <div className="rec-action">{ACTION_LABEL[recommendation.action] || recommendation.action}</div>
          <div className="rec-approval-status">
            {recommendation.status === "applied" ? "Route changed"
              : recommendation.status === "rejected" ? "Recommendation rejected"
                : recommendation.extra?.approval_validation?.status === "unverified"
                  ? "Cannot currently be verified"
                  : "Waiting for approval"}
          </div>
          {recommendation.extra?.monitor_source === "tomtom" && (
            <div className="traffic-alert">
              <b>{recommendation.extra.callsign}</b>
              {recommendation.extra.incident && (
                <div>
                  TomTom · {recommendation.extra.incident.type}: {recommendation.extra.incident.description}
                  <br />
                  Incident {recommendation.extra.incident.external_id} · {recommendation.extra.incident.distance_to_route_m} m from route
                  {recommendation.extra.incident.last_checked_at
                    ? ` · ${new Date(recommendation.extra.incident.last_checked_at * 1000).toLocaleTimeString()}`
                    : ""}
                </div>
              )}
              <div>
                Route {recommendation.extra.route_a?.label}: {recommendation.extra.route_a?.eta_min} min
                {" · "}{recommendation.extra.route_a?.status}
              </div>
              {recommendation.extra.route_b ? (
                <div className="traffic-route-b">
                  Alternative {recommendation.extra.route_b.label}: {recommendation.extra.route_b.eta_min} min
                  {" · "}{recommendation.extra.route_b.distance_km} km
                  {recommendation.extra.estimated_saving_min != null
                    ? ` · saves about ${recommendation.extra.estimated_saving_min} min` : ""}
                  <br />Traffic check: {recommendation.extra.route_b.status}
                </div>
              ) : (
                <div className="traffic-route-b">No validated alternative route is currently available.</div>
              )}
            </div>
          )}
          <div className="rec-explanation">
            {explanationRows(recommendation.reason).map((row, index) => (
              <div className="rec-explanation-row" key={`${row.label}-${index}`}>
                {row.label && <b>{row.label}</b>}
                <span>{row.text}</span>
              </div>
            ))}
          </div>
          {recommendation.extra?.agent_trace?.length > 0 && (
            <details className="advanced">
              <summary>Technical agent trace</summary>
              <div className="agent-trace">
                {recommendation.extra.agent_trace.map((trace, index) => (
                  <div key={`${trace.agent}-${index}`}><b>{trace.agent}</b>: {trace.message}</div>
                ))}
              </div>
            </details>
          )}
          {err && <div className="note err">{err}</div>}
          <div className="rec-actions">
            <button className="rec-apply" onClick={() => decide("apply")} disabled={working}>
              {working ? "Working…" : "Accept"}
            </button>
            <button className="rec-reject" onClick={() => decide("reject")} disabled={working}>Ignore</button>
          </div>
        </div>
      ) : (
        <div className="empty-state">
          Nothing to decide right now. Use the buttons at the bottom to try out an incident.
        </div>
      )}

      {mission && (
        <details className="advanced">
          <summary>Advanced: run the AI agent team</summary>
          <button className="rec-apply" style={{ width: "100%", margin: "8px 0" }} disabled={agentBusy} onClick={onRunAgents}>
            {agentBusy ? "Agents working…" : "Run agent team (LangGraph)"}
          </button>
          {agentTrace?.length > 0 && (
            <div className="agent-trace">
              {agentTrace.map((t, i) => (<div key={i}><b>{t.agent}</b>: {t.message}</div>))}
            </div>
          )}
        </details>
      )}
    </div>
  );
}
