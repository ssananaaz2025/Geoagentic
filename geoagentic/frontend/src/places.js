import { api } from "./api";

let cache = null;

/** Built-in Bengaluru places (hospitals, stations, landmarks) from the backend. */
export async function loadPlaces() {
  if (cache) return cache;
  try {
    cache = (await api.getPlaces()).results || [];
  } catch {
    return [];            // don't cache a failure; try again next time
  }
  return cache;
}

function distM(aLat, aLng, bLat, bLng) {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (bLat - aLat) * rad, dLng = (bLng - aLng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(aLat * rad) * Math.cos(bLat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Friendly name for a map click: "Near Cubbon Park" or the raw coordinates. */
export function nameForPoint(lat, lng, places = cache || []) {
  let best = null, bestD = Infinity;
  for (const p of places) {
    const d = distM(lat, lng, p.lat, p.lng);
    if (d < bestD) { best = p; bestD = d; }
  }
  if (best && bestD < 700) return `Near ${best.name}`;
  return `Pinned spot (${lat.toFixed(4)}, ${lng.toFixed(4)})`;
}

export const BLR = { minLat: 12.83, maxLat: 13.14, minLng: 77.45, maxLng: 77.75 };
export const inBengaluru = (lat, lng) =>
  lat >= BLR.minLat && lat <= BLR.maxLat && lng >= BLR.minLng && lng <= BLR.maxLng;
