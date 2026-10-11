import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import LocationPicker from "./LocationPicker.jsx";

const TYPE_ICON = { ambulance: "🚑", fire: "🚒", police: "🚓" };

function straightKm(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/**
 * Dispatch panel: pick a vehicle, pick FROM, pick TO, press Dispatch.
 * The From / To values live in the dashboard (`draft`) so the map can show
 * them and let the user click to set them.
 */
export default function TripSetup({
  vehicles, selectedVehicleId, onSelectVehicle,
  draft, setDraft, pickMode, setPickMode,
  onMissionCreated, onVehicleCreated,
}) {
  const [newMode, setNewMode] = useState(false);
  const [callsign, setCallsign] = useState("");
  const [vtype, setVtype] = useState("ambulance");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [autoOrigin, setAutoOrigin] = useState(true);   // false once the user clears/changes "From" by hand

  useEffect(() => {            // success note fades on its own
    if (!ok) return;
    const t = setTimeout(() => setOk(""), 8000);
    return () => clearTimeout(t);
  }, [ok]);

  const vehicle = vehicles.find((v) => v.id === selectedVehicleId) || null;

  // Default "From" = where the selected vehicle is right now (can be changed).
  useEffect(() => {
    if (newMode || !vehicle) return;
    if (autoOrigin && (!draft.origin || draft.origin.auto)) {
      setDraft((d) => ({
        ...d,
        origin: { name: `${vehicle.callsign}'s current position`, lat: vehicle.lat, lng: vehicle.lng, auto: true },
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicle?.id, newMode, autoOrigin, draft.origin === null]);

  const km = useMemo(() => (draft.origin && draft.dest ? straightKm(draft.origin, draft.dest) : null), [draft]);
  const same = km !== null && km < 0.05;

  const missing = newMode && !callsign.trim() ? "Enter a callsign for the new vehicle"
    : !newMode && !vehicle ? "Choose a vehicle"
    : !draft.origin ? (newMode ? "Choose where the new vehicle starts" : "Choose where to start")
    : !draft.dest ? "Choose a destination"
    : same ? "Start and destination are the same place"
    : "";

  async function dispatch() {
    setBusy(true); setError(""); setOk("");
    try {
      let vehicleId = selectedVehicleId;
      let label = vehicle?.callsign;
      const start = draft.origin.auto && vehicle && !newMode
        ? { lat: vehicle.lat, lng: vehicle.lng } : draft.origin;
      if (newMode) {
        const v = await api.createVehicle({
          callsign: callsign.trim(), vehicle_type: vtype,
          lat: start.lat, lng: start.lng,
        });
        onVehicleCreated?.(v);
        vehicleId = v.id; label = v.callsign;
      }
      const mission = await api.createMission({
        vehicle_id: vehicleId,
        origin_lat: start.lat, origin_lng: start.lng,
        dest_lat: draft.dest.lat, dest_lng: draft.dest.lng,
      });
      const best = mission.routes?.[0];
      setOk(`${label} dispatched${best ? ` — ${best.distance_km.toFixed(1)} km, about ${Math.max(1, Math.round(best.base_eta_min))} min` : ""}.`);
      setDraft({ origin: null, dest: null });
      setAutoOrigin(true);
      setPickMode(null);
      setNewMode(false); setCallsign("");
      onMissionCreated(mission, vehicleId);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const togglePick = (which) => setPickMode(pickMode === which ? null : which);

  return (
    <div className="dispatch-card">
      <div className="step-title"><span className="step-num">1</span>Vehicle</div>
      <select
        className="plain-select"
        value={newMode ? "__new__" : (selectedVehicleId || "")}
        onChange={(e) => {
          if (e.target.value === "__new__") {
            // a new vehicle starts wherever the user says, not at another vehicle's position
            setNewMode(true); setAutoOrigin(false);
            setDraft((d) => (d.origin?.auto ? { ...d, origin: null } : d));
          }
          else { setNewMode(false); setAutoOrigin(true); onSelectVehicle(e.target.value); }
        }}
      >
        {vehicles.map((v) => (
          <option key={v.id} value={v.id}>
            {TYPE_ICON[v.vehicle_type] || "🚗"} {v.callsign} — {v.status === "idle" ? "free" : v.status.replace("_", " ")}
          </option>
        ))}
        <option value="__new__">＋ Add a new vehicle…</option>
      </select>

      {newMode && (
        <div className="new-vehicle-row">
          <input placeholder="Callsign, e.g. AMB-03" value={callsign} onChange={(e) => setCallsign(e.target.value)} />
          <select value={vtype} onChange={(e) => setVtype(e.target.value)}>
            <option value="ambulance">Ambulance</option>
            <option value="fire">Fire engine</option>
            <option value="police">Police</option>
          </select>
        </div>
      )}
      {!newMode && vehicle && vehicle.status === "enroute" && (
        <div className="note warn">{vehicle.callsign} is already on a trip. Dispatching again replaces it.</div>
      )}

      <div className="step-title"><span className="step-num">2</span>Route</div>
      <LocationPicker
        label={newMode ? "New vehicle starts at" : "From"} color="#16a34a" placeholder="Search a place or area…"
        value={draft.origin}
        onChange={(v) => { if (v === null) setAutoOrigin(false); setDraft((d) => ({ ...d, origin: v })); }}
        picking={pickMode === "origin"} onTogglePick={() => togglePick("origin")}
        extraAction={vehicle && !newMode ? {
          label: "🚑 Use vehicle position",
          onClick: () => { setAutoOrigin(true); setDraft((d) => ({
            ...d, origin: { name: `${vehicle.callsign}'s current position`, lat: vehicle.lat, lng: vehicle.lng, auto: true },
          })); },
        } : null}
      />
      <LocationPicker
        label="To" color="#dc2626" placeholder="Hospital, station, landmark…"
        value={draft.dest}
        onChange={(v) => setDraft((d) => ({ ...d, dest: v }))}
        picking={pickMode === "dest"} onTogglePick={() => togglePick("dest")}
      />

      {km !== null && !same && <div className="trip-sum">≈ {km.toFixed(1)} km apart (straight line)</div>}
      {error && <div className="note err">{error}</div>}
      {ok && <div className="note good">✓ {ok}</div>}

      <button className="dispatch-btn" onClick={dispatch} disabled={busy || !!missing}>
        {busy ? "Dispatching…" : "🚨 Dispatch"}
      </button>
      {missing && !busy && <div className="dispatch-why">{missing}</div>}
    </div>
  );
}
