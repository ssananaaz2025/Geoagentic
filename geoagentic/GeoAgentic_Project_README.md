# GeoAgentic — Emergency Vehicle Movement in Urban Cities

## 1. Project Overview

GeoAgentic is an intelligent decision-support framework planned for emergency vehicle movement in urban cities.

The problem: an ambulance, fire engine, or police vehicle may start with a good route, but traffic, accidents, road closures, or vehicle deviations can change conditions during the mission.

The intended system continuously:
1. Observes the emergency mission
2. Detects important changes
3. Understands the incident
4. Estimates ETA/delay and mission impact
5. Evaluates alternative routes/resources
6. Explains the recommendation
7. Keeps a human operator in the loop
8. Acts/replans and observes again

Example:

```text
Ambulance starts
      ↓
Original ETA = 10 min
      ↓
Accident occurs on route
      ↓
Traffic/incident data changes
      ↓
Incident detected
      ↓
ETA becomes 18 min
      ↓
Alternative routes evaluated
      ↓
Alternative ETA = 12 min
      ↓
System recommends rerouting
      ↓
Human operator approves
```

> Current status: this is an early working prototype, not yet the complete GeoAgentic AI system.

---

# 2. Current Status

## DONE

### Backend / API validation
- [x] FastAPI backend created and running
- [x] Swagger/OpenAPI documentation available at `/docs`
- [x] `/health` tested
- [x] OSM/Nominatim geocoding tested
- [x] TomTom traffic incident API integrated
- [x] Google Routes API integrated
- [x] OpenStreetMap data used for map tiles and Nominatim geocoding
- [x] `/api/validate/live` available
- [x] Repeated live polling validation completed
- [x] `live_validation_results.csv` generated
- [x] SUMO scenario/script files are present
- [x] GET incidents endpoint fixed to use query parameters instead of a GET body

### Frontend
- [x] React + Vite frontend created
- [x] Leaflet installed
- [x] React-Leaflet installed
- [x] Real OpenStreetMap map displayed
- [x] Bengaluru map displayed
- [x] Location search added
- [x] Search result moves the map
- [x] Search marker displayed

## NOT DONE
- [ ] TomTom incidents displayed on React map
- [ ] Google route drawn on React map
- [ ] OSRM routing service integrated (open-source route + alternatives, no API key)
- [ ] LangGraph agent orchestration
- [ ] Live ambulance GPS tracking
- [ ] Route deviation detection
- [ ] ETA/delay impact engine
- [ ] Alternative-route decision logic
- [ ] SUMO + TraCI end-to-end experiment
- [ ] Incident/event fusion
- [ ] Multi-agent system
- [ ] LLM reasoning/tool calling
- [ ] MCP tools
- [ ] RAG
- [ ] ML/DL prediction
- [ ] Final evaluation dashboard
- [ ] Closed-loop emergency decision workflow

---

# 3. Current Architecture

```text
                    EXTERNAL DATA
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
 OpenStreetMap        TomTom            Google
 / Nominatim          Traffic           Routes
 Geocoding            Incidents         Routing
        │                │                 │
        │                │           (+ OSRM planned:
        │                │            open routing on
        │                │            OSM road network)
        └────────────────┼─────────────────┘
                         ▼
                     FastAPI
                    BACKEND
                         │
                         ▼
                  React + Leaflet
                         │
                         ▼
                    REAL MAP UI
```

The final architecture will extend this into:

```text
GPS / TomTom / OSM / Google / IoT
              ↓
       Data Ingestion
              ↓
        Event Fusion
              ↓
      Unified World State
              ↓
      GeoAgentic Supervisor
        (LangGraph)
       ┌──────┼─────────┐
       ↓      ↓         ↓
 Trajectory Incident  ETA/Impact
   Agent     Agent      Agent
       └──────┼─────────┘
              ↓
         Route Agent
              ↓
       Resource Agent
              ↓
     LLM + MCP + RAG + ML/DL
              ↓
       Explainable Decision
              ↓
       Human Operator
              ↓
       React Dashboard
              ↑
         SUMO + TraCI
       (simulation/testing)
```

---

# 4. What We Have Done

## 4.1 FastAPI Backend

FastAPI is the Python backend framework.

It acts as the central layer between the frontend and external services.

```text
React
  ↓
FastAPI
  ↓
External APIs
```

Backend:

```text
http://127.0.0.1:8000
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

Why we use it:
- centralizes external API calls
- keeps API keys/configuration on the backend
- provides structured endpoints
- gives Swagger documentation
- gives one place for future AI/agent logic

---

## 4.2 OpenStreetMap

OpenStreetMap provides real geographic map information such as roads, buildings, parks and landmarks.

The current map is **real geographic map data**, not a manually drawn fake map.

OpenStreetMap (OSM) is our underlying map/road-network data. Think of it as
the digital road map that other tools operate on. It is open data, so:
- Leaflet draws OSM tiles for the visible map
- Nominatim searches OSM data to convert place names into coordinates
- OSRM (planned) builds its routing graph from OSM roads
- SUMO (planned) can import OSM roads to build a simulation network

Important distinction:

```text
OpenStreetMap = geographic/map data
Leaflet = interactive map display
React = web application
```

---

## 4.3 Nominatim / OSM Geocoding

Nominatim is used for location search/geocoding.

Example:

```text
"PES University RR Campus"
          ↓
latitude + longitude
          ↓
Leaflet moves map
          ↓
marker appears
```

Why:
Users should be able to search names/addresses instead of manually entering coordinates.

Current frontend has a search box.

The current implementation can search places such as:

```text
PES University RR Campus
BMS College of Engineering
other locations
```

Search quality depends on the available map/index data and query wording.

---

## 4.4 Leaflet

Leaflet is the interactive mapping library.

It handles:
- zoom
- pan
- markers
- popups
- map layers
- future route lines

It does not itself provide the geographic map.

Current relationship:

```text
OpenStreetMap tiles
       ↓
Leaflet
       ↓
React
       ↓
Browser
```

---

## 4.5 React + Vite

React is the frontend framework.

It will eventually display:
- emergency vehicles
- incidents
- routes
- ETA
- delay
- alerts
- recommendations
- explanations
- operator controls

Vite is the development/build tool.

Current frontend:

```text
http://localhost:5173/
```

Project:

```text
geoagentic-map/
├── src/
│   ├── App.jsx
│   └── main.jsx
├── public/
└── package.json
```

---

## 4.6 TomTom Traffic Incident API

TomTom is currently used as the external traffic/incident data source.

It can provide traffic incident information such as reported incidents, congestion-related events and restrictions/closures depending on the returned data.

Current flow:

```text
TomTom
   ↓
traffic/incident data
   ↓
FastAPI
   ↓
future GeoAgentic processing
   ↓
React map
```

Why:
A normal map tells us where roads are. The GeoAgentic system also needs information about what is happening on those roads.

Future example:

```text
🚧 Accident
⚠️ Road closure
🚗 Congestion
```

will be displayed near the ambulance route.

---

## 4.7 Google Routes API

Google Routes API is used for route calculation and travel-time information.

It can provide route-related information such as:
- route geometry
- distance
- travel duration
- alternative route information when requested/supported

Example:

```text
Origin → Destination

Route A = 10 min
Route B = 12 min
Route C = 18 min
```

Why:
If the current route becomes bad, GeoAgentic needs route information to evaluate alternatives.

Important:

```text
Map display       → OpenStreetMap + Leaflet
Route calculation → Google Routes API
```

Google Routes API is not the same as displaying Google Maps.

---

## 4.8 OSRM (Open Source Routing Machine) — Planned

OSRM is an open-source routing engine that runs on OpenStreetMap road data.
When you give it a start and a destination, its **Route service** finds the
fastest route according to the routing profile/weights (e.g. car).

Relationship to the other pieces:

```text
OpenStreetMap = the road map (data)
OSRM          = the engine that finds routes on that map
```

What the Route service returns:
- route geometry (polyline / GeoJSON) that Leaflet can draw directly
- distance and duration
- turn-by-turn steps (optional)
- alternative routes (`alternatives=true`)

Example request (public demo server; note lon,lat order):

```text
GET https://router.project-osrm.org/route/v1/driving/
    77.5946,12.9716;77.6245,12.9352
    ?overview=full&geometries=geojson&alternatives=true&steps=false
```

Planned backend endpoint (mirrors `POST /api/routes`):

```text
POST /api/routes/osrm
```

Why we add it alongside Google Routes:

| | OSRM | Google Routes API |
|---|---|---|
| Cost / key | Free, open source, no key | Paid, API key required |
| Road data | OpenStreetMap | Google's own |
| Live traffic in ETA | **No** — static weights (speed limits/profile) | Yes (traffic-aware duration) |
| Hosting | Public demo server or self-hosted | Google-hosted |
| Best used for | Baseline routes, alternatives, offline/SUMO-consistent experiments | Traffic-aware ETA |

Important caveats:
- OSRM does not know about live traffic or incidents. The delay caused by an
  incident must come from TomTom + the GeoAgentic impact engine, not from OSRM.
- The public demo server (`router.project-osrm.org`) is for light testing
  only: fair-use limits, driving profile only, no uptime guarantee. For real
  experiments, self-host OSRM with a Bengaluru/Karnataka OSM extract.
- Because OSRM and SUMO can both use the same OSM extract, they give a
  consistent road network for controlled experiments.

---

## 4.9 Live API Validation

A live validation workflow was created to test external sources repeatedly.

A completed experiment used:

```text
5 cycles
60-second interval
3 sources
15 source observations
```

Results were saved to:

```text
live_validation_results.csv
```

Why:
A research project should show more than a single successful API call.

Useful validation measures include:
- success rate
- latency
- failure rate
- missing data
- freshness
- repeated availability

---

# 5. Current API Endpoints

Examples:

```text
GET /health
```

Checks backend health.

```text
GET /api/osm/geocode?query=...
```

Location search/geocoding.

```text
GET /api/incidents?min_lon=...&min_lat=...&max_lon=...&max_lat=...
```

Traffic incident retrieval for a geographic bounding box.

```text
POST /api/routes
```

Route calculation.

Example request concept:

```json
{
  "origin": {"lat": 12.9716, "lon": 77.5946},
  "destination": {"lat": 12.9352, "lon": 77.6245},
  "compute_alternative_routes": true
}
```

```text
POST /api/routes/osrm        (planned)
```

Route calculation via OSRM on OpenStreetMap data.

```text
GET /api/validate/live
```

Runs live validation.

---

# 6. What Is NOT Done Yet

## 6.1 TomTom Incident Markers

Current state:

```text
TomTom → FastAPI
```

but not yet:

```text
TomTom
  ↓
FastAPI
  ↓
React
  ↓
🚧 marker
```

Goal:
Display real incident locations on the map.

Why:
The operator needs to see whether an incident is close to or on the emergency vehicle's route.

---

## 6.2 Google Route Visualization

Current backend route calculation exists, but the route is not yet fully drawn on the React map.

Planned:

```text
Origin
  ↓
Google Routes
  ↓
route geometry + ETA
  ↓
React
  ↓
Leaflet polyline
  ↓
🛣️ route
```

Why:
The operator must visually understand the current route and later compare alternatives.

---

## 6.3 Ambulance GPS Tracking

Current marker is static.

It is NOT a live ambulance.

Future:

```text
Ambulance GPS
      ↓
lat/lon
      ↓
FastAPI
      ↓
React
      ↓
🚑 moving marker
```

Why:
GeoAgentic needs the actual vehicle trajectory to detect deviation and estimate mission status.

---

## 6.4 Route Deviation Detection

Compare:

```text
Planned route
      VS
Actual vehicle trajectory
```

Example:

```text
Planned:
A → B → C → D

Actual:
A → B → E → F → D
```

Why:
The system needs to know whether the emergency vehicle has left its expected route.

Possible measurements:
- distance from planned route
- GPS trajectory
- heading
- route matching
- time away from route

---

## 6.5 ETA / Delay Prediction

Example:

```text
Original ETA = 10 min
Current ETA = 17 min
Delay = +7 min
```

Future impact component can use:
- vehicle location
- traffic
- incidents
- route
- travel time
- prediction model

Why:
An incident matters operationally only when we understand its effect on the mission.

---

## 6.6 Alternative Route Recommendation

Route calculation is not the same as decision-making.

Example:

```text
Route A: 18 min + accident
Route B: 12 min + no major incident
Route C: 15 min + moderate congestion
```

The Route Agent will evaluate route information and produce an explainable recommendation.

Why:
The goal is not merely to show routes; it is to support an emergency-mission decision.

Human/operator approval remains part of the intended architecture.

---

# 7. SUMO + TraCI

## What is SUMO?

SUMO = Simulation of Urban MObility.

It is a traffic/mobility simulator.

It can simulate:
- roads
- vehicles
- traffic
- congestion
- intersections
- emergency vehicles
- controlled scenarios

SUMO is **not the AI agent**.

It is the environment used to test the GeoAgentic system.

```text
OSM
 ↓
SUMO
 ↓
traffic simulation
 ↓
ambulance
 ↓
accident/congestion
 ↓
GeoAgentic
```

## What is TraCI?

TraCI is the programmatic interface for interacting with SUMO.

It allows Python to:
- read vehicle positions
- read traffic state
- control simulation
- create/change simulation events
- observe vehicle movement

Why:
We need controlled experiments where we know the ground truth.

Example:

```text
At simulation time 100:
Create accident on road R2

SUMO:
traffic speed decreases

GeoAgentic:
detects incident
calculates impact
evaluates alternative

Then compare:
Predicted event vs known SUMO ground truth
```

This is useful for research evaluation.

---

# 8. Incident Detection / Event Fusion

Future system should not depend on one signal only.

Example:

```text
Ambulance speed:
42 km/h → 8 km/h

       +

TomTom:
accident reported

       +

Travel time:
increased

       ↓

Strong evidence that an incident
is affecting the mission
```

Why:
One source may be incomplete or noisy.

This is the basis for a multi-source incident engine.

---

# 9. Multi-Agent GeoAgentic System

This is not implemented yet.

Planned agents:

### Trajectory Agent
Answers:
- Where is the vehicle?
- Has it deviated?
- What is its current movement?

### Incident Agent
Answers:
- What happened?
- Where?
- What type?
- How confident is the detection?

### ETA/Impact Agent
Answers:
- How much delay?
- What is the mission impact?

### Route Agent
Answers:
- What alternatives exist?
- What are their estimated travel times?

### Resource Agent
Answers:
- Is another emergency vehicle nearby?
- Should backup resources be considered?

### Supervisor / Orchestrator
Coordinates the specialized agents.

Architecture:

```text
                 Supervisor
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
  Trajectory     Incident      ETA/Impact
     Agent          Agent          Agent
       └─────────────┼─────────────┘
                     ▼
                Route Agent
                     │
                Resource Agent
```

Why multi-agent:
Each task has a different responsibility, data requirement, and reasoning process.

## 9.1 Framework: LangGraph

Chosen orchestration framework: **LangGraph** (Python, from LangChain Inc.),
a library for building stateful agent workflows as a graph.

Why LangGraph fits GeoAgentic:

| GeoAgentic need | LangGraph feature |
|---|---|
| Shared "Unified World State" | Typed graph state passed between nodes |
| Supervisor + specialist agents | Supervisor pattern; each agent is a node |
| Observe → decide → act → observe again | Cyclic graphs (loops), not just linear chains |
| Human operator approves reroute | Human-in-the-loop `interrupt()` pauses the graph until approve/reject |
| Long-running missions | Checkpointing/persistence to resume after restarts |
| Explainability / evaluation | Traceable step-by-step execution |
| Works with our FastAPI backend | Runs as a normal Python library |

Planned graph:

```text
START
  ↓
observe (GPS, TomTom, routing)
  ↓
Supervisor ──► Trajectory Agent
           ──► Incident Agent
           ──► ETA/Impact Agent
           ──► Route Agent (Google Routes + OSRM)
           ──► Resource Agent
  ↓
recommendation + explanation (LLM)
  ↓
interrupt → Human operator: APPROVE / REJECT
  ↓
act (replan) ──► back to observe
```

Rules of use:
- Agents call deterministic tools (APIs, routing, ML) for facts and numbers;
  the LLM only reasons over and explains the results.
- No reroute is applied without operator approval.
- Tools are exposed to agents through MCP (Section 11) or as plain LangGraph tools.

---

# 10. LLM

Not implemented yet.

The LLM should not replace APIs, routing algorithms, or numerical prediction.

Good uses:
- situation summarization
- natural-language questions
- decision explanation
- tool selection
- combining structured outputs into operator-readable explanations

Example:

```text
Operator:
Why was the ambulance rerouted?

System:
An accident was detected on the original route.
The incident increased estimated travel time.
An alternative route was evaluated with lower
estimated travel time. The system recommends
the alternative route for operator approval.
```

Why:
LLMs are useful for reasoning and communication, while deterministic tools/models should provide the underlying data and calculations.

---

# 11. MCP

MCP = Model Context Protocol.

Planned use: provide standardized tools for agents.

Possible tools:

```text
get_vehicle_location()
get_vehicle_status()
get_traffic()
get_incidents()
get_route()
calculate_eta()
find_alternative_routes()
get_nearby_emergency_vehicles()
get_emergency_policy()
```

Concept:

```text
Agent / LLM
     ↓
    MCP
     ↓
Structured tools
     ↓
GPS / Traffic / Routing / Database
```

Why:
It provides a structured way for agents to access tools instead of putting every integration directly into the LLM logic.

---

# 12. RAG

RAG = Retrieval-Augmented Generation.

Future system can store:
- emergency SOPs
- hospital information
- operational policies
- emergency vehicle rules
- procedures

Flow:

```text
Agent
  ↓
Retrieve relevant policy
  ↓
Document/context
  ↓
LLM
  ↓
Grounded explanation
```

Why:
Project-specific emergency policies should come from project documents rather than relying only on general LLM knowledge.

---

# 13. ML / DL

Not implemented yet.

ML/DL will be used where prediction is needed.

Possible application:

```text
Traffic
Vehicle position
Road
Time
Historical travel time
       ↓
ML model
       ↓
Predicted ETA
```

Another possible task:

```text
Incident
Severity
Traffic
Road characteristics
Vehicle state
       ↓
ML/DL model
       ↓
Expected delay
```

Possible future models:
- Random Forest
- XGBoost
- neural networks
- temporal models
- graph-based models

Why:
The research value comes from measuring whether prediction improves over suitable baselines.

---

# 14. Digital Twin / Simulation

SUMO can serve as a controlled representation of the road/traffic environment.

It can support what-if experiments:

```text
What if an accident occurs here?
What if Route B is selected?
What if congestion increases?
What if another ambulance is dispatched?
```

Why:
Real-world emergency events are difficult to reproduce safely and consistently. Simulation provides repeatable experiments.

---

# 15. Planned Final Dashboard

```text
┌───────────────────────────────────────────────────────────┐
│ GEOAGENTIC EMERGENCY OPERATIONS DASHBOARD                │
├───────────────────────────────────────────────────────────┤
│                                                           │
│  🚑 Ambulance A12                                         │
│  Mission: Hospital → Destination                         │
│  ETA: 12 min       Delay: +2 min                         │
│                                                           │
│                    REAL-TIME MAP                          │
│                                                           │
│       🚑 ───────────── 🚧 ───────────── 🏥               │
│                 ╲                                       │
│                  ╲ Alternative Route                    │
│                                                           │
├───────────────────────────────────────────────────────────┤
│ INCIDENT                                                  │
│ Accident detected on current route                       │
│                                                           │
│ IMPACT                                                    │
│ Original ETA: 10 min                                      │
│ Current ETA: 17 min                                       │
│                                                           │
│ RECOMMENDATION                                            │
│ Alternative route evaluated                              │
│                                                           │
│ EXPLANATION                                               │
│ Accident-related congestion is affecting the route.      │
│                                                           │
│             [ APPROVE ]       [ REJECT ]                  │
└───────────────────────────────────────────────────────────┘
```

---

# 16. Development Roadmap

### Phase 1 — DONE
```text
React
 ↓
Leaflet
 ↓
OpenStreetMap
 ↓
Location search
```

### Phase 2 — NEXT
```text
FastAPI
 ↓
TomTom incidents
 ↓
React
 ↓
Incident markers
```

### Phase 3
```text
Google Routes (+ OSRM)
 ↓
Route geometry + ETA
 ↓
React
 ↓
Route visualization
```

### Phase 4
```text
Ambulance GPS
 ↓
FastAPI
 ↓
React
 ↓
Moving ambulance
```

### Phase 5
```text
Planned route
+
Actual trajectory
 ↓
Deviation detection
```

### Phase 6
```text
Traffic + incidents + GPS
 ↓
Impact engine
 ↓
ETA / delay
```

### Phase 7
```text
SUMO + TraCI
 ↓
Emergency traffic simulation
 ↓
Known incidents / ground truth
 ↓
GeoAgentic evaluation
```

### Phase 8 (built with LangGraph)
```text
Trajectory Agent
Incident Agent
ETA Agent
Route Agent
Resource Agent
Supervisor
```

### Phase 9
```text
LLM
+
Tool Calling
+
MCP
+
RAG
```

### Phase 10
```text
ML/DL prediction
+
Experiments
+
Evaluation
+
Research results
```

---

# 17. Final Technology Responsibility Table

| Technology | What it does | Why we use it |
|---|---|---|
| OpenStreetMap | Real geographic map data | Base geographic environment |
| Nominatim | Location search/geocoding | Convert place names to coordinates |
| OSRM | Open-source routing engine on OSM data | Free baseline routes/alternatives, SUMO-consistent road network |
| Leaflet | Interactive maps | Display markers/routes/layers |
| React | Frontend/dashboard | Build the user interface |
| Vite | Frontend development/build | Run React efficiently |
| FastAPI | Backend | Connect frontend, APIs, future AI |
| TomTom | Traffic/incident data | Know what is happening on roads |
| Google Routes | Route/travel-time calculation | Evaluate route options |
| Ambulance GPS | Vehicle position | Track actual mission |
| SUMO | Traffic simulation | Controlled testing |
| TraCI | SUMO programmatic interface | Connect Python/AI to simulation |
| ML/DL | Prediction | ETA/impact prediction |
| LLM | Reasoning/explanation | Natural-language decision support |
| MCP | Tool interface | Standardized agent tool access |
| RAG | Document retrieval | Use project-specific policies/SOPs |
| Multi-agent system | Specialized reasoning | Separate mission tasks |
| LangGraph | Agent orchestration framework | Stateful graph, supervisor, loops, human-in-the-loop approval |
| Human operator | Final approval | Keep emergency decisions human-supervised |

---

# 18. Key Architectural Principle

Do **not** make the LLM responsible for everything.

Use each component for what it is good at:

```text
Map data       → OpenStreetMap
Search         → Nominatim
Visualization  → Leaflet
Backend        → FastAPI
Traffic        → TomTom
Routing        → Google Routes (traffic-aware) + OSRM (open, OSM-based)
Vehicle state  → GPS
Simulation     → SUMO
Prediction     → ML/DL
Reasoning      → LLM
Tools          → MCP
Knowledge      → RAG
Coordination   → Multi-agent supervisor (LangGraph)
Final approval → Human operator
```

---

# 19. Current One-Line Project Definition

> GeoAgentic is an explainable, multi-source, multi-agent emergency decision-support framework that continuously monitors emergency vehicle missions, detects incidents and route deviations, estimates mission impact, evaluates alternative routes and resources, and provides human-approved recommendations through a real-time geospatial dashboard.

---

# 20. Immediate Next Task

The next implementation task is:

**Connect the existing FastAPI `/api/incidents` endpoint to the React/Leaflet map and display TomTom traffic incidents as real map markers.**

Then:

1. Google route visualization
2. Ambulance marker
3. Ambulance movement
4. Route deviation
5. ETA/delay
6. SUMO + TraCI
7. Event fusion
8. Multi-agent orchestration
9. LLM/tool calling
10. MCP + RAG
11. ML/DL prediction
12. Final evaluation
