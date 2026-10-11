/**
 * Thin fetch wrapper around the GeoAgentic backend.
 * In dev, Vite proxies /api -> http://127.0.0.1:8000 (see vite.config.js).
 * In production, set VITE_API_BASE to the deployed backend URL.
 *
 * Errors are turned into short, human-readable messages (err.message) and
 * err.offline is true when the backend simply can't be reached, so the UI
 * can say so instead of failing silently in the console.
 */
export const BASE = import.meta.env.VITE_API_BASE || "";

function friendly(detail) {
  if (!detail) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => String(d.msg || d).replace(/^Value error,\s*/i, "")).join(" · ");
  }
  return null;
}

async function req(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    const e = new Error("Can't reach the backend. Is it running on port 8000?");
    e.offline = true;
    throw e;
  }
  const ct = res.headers.get("content-type") || "";
  if (!res.ok) {
    let msg = null;
    if (ct.includes("json")) {
      const body = await res.json().catch(() => null);
      msg = friendly(body?.detail);
    }
    // Vite's dev proxy answers 500/502/503/504 with a non-JSON body when
    // nothing is listening on :8000.
    const e = new Error(msg || (res.status >= 500 && !ct.includes("json")
      ? "Can't reach the backend. Is it running on port 8000?"
      : `Request failed (${res.status})`));
    e.status = res.status;
    e.offline = res.status >= 500 && !ct.includes("json");
    throw e;
  }
  return ct.includes("json") ? res.json() : null;
}

const post = (path, body) => req(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  getState: () => req("/api/state"),
  listVehicles: () => req("/api/vehicles"),
  createVehicle: (body) => post("/api/vehicles", body),
  createMission: (body) => post("/api/missions", body),
  getMission: (id) => req(`/api/missions/${id}`),
  createIncident: (body) => post("/api/incidents", body),
  triggerDeviation: (vehicleId, offsetM = 400) => post(`/api/vehicles/${vehicleId}/deviate?offset_m=${offsetM}`),
  applyRecommendation: (recId) => post(`/api/recommendations/${recId}/apply`),
  rejectRecommendation: (recId) => post(`/api/recommendations/${recId}/reject`),
  getTimeline: (missionId) => req(`/api/timeline${missionId ? `?mission_id=${missionId}` : ""}`),
  reset: () => post("/api/reset"),
  runAgents: (missionId) => post("/api/agents/run", { mission_id: missionId }),
  resumeAgents: (threadId, decision) => post(`/api/agents/runs/${threadId}/resume`, { decision }),
  previewRoutes: (body) => post("/api/routes/preview", body),
  liveIncidents: () => req("/api/live/incidents"),
  checkMissionTraffic: (missionId) => post(`/api/traffic/check/${missionId}`),
  getTrafficStatus: () => req("/api/traffic/status"),
  geocode: (q) => req(`/api/geocode?q=${encodeURIComponent(q)}`),
  getPlaces: () => req("/api/places"),
  getConfigStatus: () => req("/api/config/status"),
};
