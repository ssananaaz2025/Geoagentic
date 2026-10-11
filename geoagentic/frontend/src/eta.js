import { secondsSinceSnapshot } from "./time";

/**
 * Remaining travel time for a vehicle on a route, in minutes.
 *
 * The old UI showed route.base_eta_min + delay (the whole-trip estimate), so
 * it never counted down as the vehicle moved. Here we use what is left:
 *   distance still to cover / current speed
 * and extrapolate between the 2-second polls so the countdown ticks every
 * second instead of jumping.
 */
export function liveProgress(vehicle, route) {
  if (!vehicle || !route) return 0;
  let p = vehicle.route_progress || 0;
  const moving = vehicle.status === "enroute" || vehicle.status === "off_route";
  if (moving && vehicle.speed_kmh > 0 && route.distance_km > 0) {
    p += (vehicle.speed_kmh * (secondsSinceSnapshot() / 3600)) / route.distance_km;
  }
  return Math.min(1, Math.max(0, p));
}

export function remainingMin(vehicle, route) {
  if (!route) return null;
  if (vehicle?.status === "completed") return 0;
  const p = liveProgress(vehicle, route);
  const kmLeft = route.distance_km * (1 - p);
  const speed = vehicle?.speed_kmh > 1 ? vehicle.speed_kmh : 0;
  if (speed) return (kmLeft / speed) * 60;
  return (route.base_eta_min + route.delay_min) * (1 - p);
}

export function kmLeft(vehicle, route) {
  if (!route) return null;
  return route.distance_km * (1 - liveProgress(vehicle, route));
}

export function fmtDuration(min) {
  if (min == null || Number.isNaN(min)) return "–";
  const totalSec = Math.max(0, Math.round(min * 60));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m >= 60) return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} m`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function arrivalClock(min) {
  if (min == null) return "–";
  return new Date(Date.now() + min * 60000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function riskLabel(score) {
  if (score > 0.5) return { text: "High", cls: "crit" };
  if (score > 0) return { text: "Some", cls: "warn" };
  return { text: "Low", cls: "ok" };
}
