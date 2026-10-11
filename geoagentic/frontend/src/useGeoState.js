import { useEffect, useRef, useState, useCallback } from "react";
import { api } from "./api";
import { recordSnapshot } from "./time";

const POLL_MS = 1500;

/**
 * Polls GET /api/state on an interval and exposes
 *   { state, fetchState, loaded, online, lastError }
 * `online` flips to false after two failed polls in a row so the UI can show
 * a "backend offline" banner (previously failures only hit the console and
 * the app just looked frozen). Polling keeps going, so it recovers by itself
 * the moment the backend comes back.
 */
export function useGeoState(pollMs = POLL_MS) {
  const [state, setState] = useState({
    vehicles: [], missions: [], incidents: [], recommendations: [], timeline: [],
  });
  const [loaded, setLoaded] = useState(false);
  const [online, setOnline] = useState(true);
  const [lastError, setLastError] = useState("");
  const fails = useRef(0);
  const inflight = useRef(false);

  const fetchState = useCallback(async () => {
    if (inflight.current) return;           // never stack requests
    inflight.current = true;
    try {
      const s = await api.getState();
      recordSnapshot(s.server_time);
      setState(s);
      setLoaded(true);
      fails.current = 0;
      setOnline(true);
      setLastError("");
    } catch (e) {
      fails.current += 1;
      setLastError(e.message);
      if (fails.current >= 2) setOnline(false);
    } finally {
      inflight.current = false;
    }
  }, []);

  useEffect(() => {
    fetchState();
    const id = setInterval(fetchState, pollMs);
    return () => clearInterval(id);
  }, [fetchState, pollMs]);

  return { state, fetchState, loaded, online, lastError };
}
