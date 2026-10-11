import React, { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { loadPlaces } from "../places";

const KIND_ICON = { hospital: "🏥", transit: "🚉", fire: "🚒", police: "🚓", landmark: "📍", area: "🏘" };
const SEARCH_STOP_WORDS = new Set(["a", "at", "around", "in", "near", "the", "to"]);
const SEARCH_SYNONYMS = {
  bangalore: "bengaluru",
  bengaluru: "bangalore",
  clinic: "hospital",
  medical: "hospital",
  metro: "transit",
  railway: "transit",
  station: "transit",
};

function searchWords(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(/\s+/).filter(Boolean);
}

function editDistanceAtMostOne(a, b) {
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length > b.length) i++;
    else if (b.length > a.length) j++;
    else { i++; j++; }
  }
  return edits + Number(i < a.length || j < b.length) <= 1;
}

function rankLocalPlaces(places, query) {
  const terms = searchWords(query).filter((word) => !SEARCH_STOP_WORDS.has(word));
  if (!terms.length) return [];
  const expandedTerms = terms.map((word) => SEARCH_SYNONYMS[word] || word);
  return places
    .map((place) => {
      const words = searchWords(`${place.name} ${place.area} ${place.kind}`)
        .flatMap((word) => [word, SEARCH_SYNONYMS[word]].filter(Boolean));
      const matches = expandedTerms.map((term) => words.find((word) =>
        word === term || word.startsWith(term) ||
        (term.length >= 5 && word.length >= 5 && editDistanceAtMostOne(term, word))
      ));
      if (matches.some((match) => !match)) return null;
      const exactPhrase = `${place.name} ${place.area}`.toLowerCase().includes(query.trim().toLowerCase());
      const exactWords = matches.filter((word, index) => word === expandedTerms[index]).length;
      return {
        place,
        score: (exactPhrase ? 100 : 0) + exactWords * 10 +
          Number(place.name.toLowerCase().startsWith(query.trim().toLowerCase())) * 20,
      };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score)
    .map(({ place }) => place);
}

/**
 * One location field (used for both "From" and "To").
 *
 * Three ways to choose, all optional:
 *   1. Type a place name - built-in Bengaluru places show instantly, live
 *      geocoder results are added when the network allows.
 *   2. Tap a suggestion from the list that opens on focus (hospitals,
 *      stations, landmarks).
 *   3. "Pick on map" - then just click the map.
 *
 * value: { name, lat, lng } | null
 */
export default function LocationPicker({
  label, color, value, onChange, picking, onTogglePick, extraAction, placeholder,
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [places, setPlaces] = useState([]);
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const wrapRef = useRef(null);
  const seq = useRef(0);

  useEffect(() => { loadPlaces().then(setPlaces); }, []);

  // close the dropdown when clicking elsewhere
  useEffect(() => {
    const h = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  // debounced search: built-in places filter instantly, geocoder follows
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setResults([]); setSearching(false); return; }
    const local = rankLocalPlaces(places, term)
      .slice(0, 6)
      .map((p) => ({ name: `${p.name}, ${p.area}`, lat: p.lat, lng: p.lng, kind: p.kind }));
    setResults(local);
    const mine = ++seq.current;
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const { results: r } = await api.geocode(term);
        if (mine !== seq.current) return;
        const seen = new Set(local.map((x) => `${x.lat.toFixed(4)},${x.lng.toFixed(4)}`));
        const extra = r.filter((x) => !seen.has(`${x.lat.toFixed(4)},${x.lng.toFixed(4)}`));
        setResults([...local, ...extra].slice(0, 8));
      } catch { /* keep local matches */ }
      if (mine === seq.current) setSearching(false);
    }, 350);
    return () => clearTimeout(t);
  }, [q, places]);

  function choose(p) {
    onChange({ name: p.name.split(",").slice(0, 2).join(",").trim(), lat: p.lat, lng: p.lng });
    setQ(""); setOpen(false);
  }

  const list = q.trim().length >= 2
    ? results
    : places.map((p) => ({ name: `${p.name}, ${p.area}`, lat: p.lat, lng: p.lng, kind: p.kind })).slice(0, 12);

  return (
    <div className="loc-field" ref={wrapRef}>
      <div className="loc-label"><span className="loc-dot" style={{ background: color }} />{label}</div>

      {value ? (
        <div className="loc-chip">
          <span className="loc-chip-name" title={value.name}>{value.name}</span>
          <button className="loc-chip-x" onClick={() => onChange(null)} title="Change">✕</button>
        </div>
      ) : (
        <div className="loc-input-wrap">
          <input
            className="loc-input"
            value={q}
            placeholder={placeholder}
            onFocus={() => setOpen(true)}
            onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          />
          {open && (
            <div className="loc-menu">
              {q.trim().length < 2 && <div className="loc-menu-head">Popular places</div>}
              {list.map((p, i) => (
                <div key={i} className="loc-item" onMouseDown={(e) => { e.preventDefault(); choose(p); }}>
                  <span>{KIND_ICON[p.kind] || "📍"}</span>
                  <span className="loc-item-name">{p.name}</span>
                </div>
              ))}
              {q.trim().length >= 2 && !list.length && !searching && (
                <div className="loc-menu-empty">No match. Try another name, or pick it on the map.</div>
              )}
              {searching && <div className="loc-menu-empty">{list.length ? "Looking for more…" : "Searching…"}</div>}
            </div>
          )}
        </div>
      )}

      {!value && (
        <div className="loc-actions">
          {onTogglePick && (
            <button className={"loc-btn" + (picking ? " on" : "")} onClick={onTogglePick}>
              🗺 {picking ? "Click on the map… (cancel)" : "Pick on map"}
            </button>
          )}
          {extraAction && (
            <button className="loc-btn" onClick={extraAction.onClick}>{extraAction.label}</button>
          )}
        </div>
      )}
    </div>
  );
}
