# GeoAgentic — Emergency Vehicle Control Room

GeoAgentic is a Bengaluru-focused emergency dispatch demo that combines a React frontend, a FastAPI backend, and a SQLite-backed decision engine to simulate real-time vehicle routing, incident handling, and operator recommendations.

It is designed to show how a dispatch control room can:
- monitor multiple emergency vehicles,
- create missions and routes,
- inject incidents (accident, traffic, flood, roadblock, hazard, etc.),
- trigger GeoAgent decision logic,
- allow a human dispatcher to approve or reject reroutes.

This project is a working demo and is intentionally scoped to a Bengaluru urban bounding box for validation and realistic route logic.

## What this app does

The product contains three primary dashboards:
- Control Room: multi-vehicle dispatch and incident management
- Driver: per-vehicle mission view and status
- Admin: system health, routing status, and fleet data

The app supports:
- live-ish vehicle simulation,
- route generation and ETA calculation,
- incident-to-recommendation workflow,
- suggestion acceptance or dismissal,
- fallback behavior when external APIs are unavailable.

## Architecture overview

### 1. Frontend
Location: `frontend/`

Technology:
- React 18
- Vite
- Leaflet + react-leaflet
- CSS

Responsibilities:
- landing page and role picker,
- three dashboards,
- map rendering and route drawing,
- mission creation,
- incident injection,
- live UI updates from backend state.

### 2. Backend
Location: `backend/app/`

Technology:
- FastAPI
- SQLAlchemy
- Pydantic
- SQLite by default

Main modules:
- `main.py`: API entrypoint, startup hooks, background simulation loop
- `config.py`: environment variables and integration toggles
- `database.py`: database engine and SQLite migration logic
- `models.py`: database schema and entities
- `schemas.py`: request/response validation
- `routing.py`: route generation and route resolution
- `decision_engine.py`: rule-based recommendation logic
- `simulation.py`: simulated GPS movement and mission progression
- `traffic_monitor.py`: route incident checks
- `agents.py`: LangGraph-based decision workflow
- `live.py`: live traffic and map data access
- `places.py`: built-in place dataset

### 3. Data layer
- Default database: SQLite file at `backend/geoagentic.db`
- Database URL can be overridden through `DATABASE_URL`
- `schema.sql` at the project root contains the portable DDL reference

### 4. External integrations (optional)
The app works without any external keys. When keys are not configured, it falls back to mock routing and templated explanations.

Optional services:
- Google Maps Directions API for real road routes
- OSRM (OpenStreetMap) for route alternatives
- Anthropic Claude for explanation phrasing
- TomTom for live incident monitoring

## Project structure

```text
geoagentic/
├── README.md
├── schema.sql
├── start.bat
├── start.sh
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   ├── geoagentic.db
│   ├── app/
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── src/
│   └── node_modules/
└── .gitignore
```

## Prerequisites

Before running the app:
- Python 3.10+
- Node.js 18+
- npm
- A terminal such as PowerShell, Command Prompt, or Bash

## Environment setup

### 1. Backend environment
From the project root:

Windows PowerShell:
```powershell
cd .\geoagentic\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Linux/macOS:
```bash
cd geoagentic/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create environment file
```bash
cp .env.example .env
```

On Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

Edit the `.env` file if you want to enable external integrations.

Example environment variables:
```env
DATABASE_URL=sqlite:///geoagentic.db
GOOGLE_MAPS_API_KEY=
GOOGLE_MAPS_REGION=in
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-5-5
TOMTOM_API_KEY=
ROUTING_PROVIDER=auto
OSRM_BASE_URL=https://router.project-osrm.org
GOOGLE_MAPS_TIMEOUT_S=3
OSRM_TIMEOUT_S=2.5
```

Notes:
- If the keys are empty, the app still runs using fallback mock data.
- The app validates all coordinates against Bengaluru bounds.
- `auto` tries Google Maps first, then OSRM, then mock routes. Provider requests
  have short configurable timeouts so a slow API is less likely to delay dispatch.

## How to run the app

### Option A: Run backend and frontend manually

#### Backend
```bash
cd geoagentic/backend
source .venv/bin/activate    # or .\.venv\Scripts\Activate.ps1 on Windows
uvicorn app.main:app --reload --port 8000
```

Then open:
- API docs: http://127.0.0.1:8000/docs
- Health endpoint: http://127.0.0.1:8000/api/health
- Config status: http://127.0.0.1:8000/api/config/status

#### Frontend
Open a second terminal:
```bash
cd geoagentic/frontend
npm install
npm run dev -- --host 0.0.0.0
```

Open in browser:
- http://localhost:5173

If port 5173 is already occupied, Vite will automatically choose another open port, such as 5174.

### Option B: Run both with helper scripts

Windows:
```powershell
cd geoagentic
start.bat
```

Linux/macOS:
```bash
cd geoagentic
chmod +x start.sh
./start.sh
```

## Working API and app behavior

The app starts with demo emergency vehicles seeded into the database:
- AMB-01
- FIRE-01
- POL-01
- AMB-02

Supported workflow:
1. Create or select a vehicle.
2. Create a mission with origin and destination.
3. Select a route.
4. Inject an incident such as accident, flood, traffic, or hazard.
5. GeoAgent evaluates the route and suggests a reroute if needed.
6. Dispatcher approves or dismisses the suggestion.

## Optional external API configuration

### Google Maps
Set:
```env
GOOGLE_MAPS_API_KEY=your_key_here
GOOGLE_MAPS_REGION=in
```
This enables real route generation and traffic-aware ETAs.
Google is tried before OSRM in `auto` mode. If a request is slow or fails, the
backend continues to the configured fallback instead of waiting for the previous
six-second default timeout. Tune the limit with `GOOGLE_MAPS_TIMEOUT_S` (default
3 seconds).

### Anthropic Claude
Set:
```env
ANTHROPIC_API_KEY=your_key_here
CLAUDE_MODEL=claude-sonnet-5-5
```
This improves explanation text for recommendations.

### TomTom
Set:
```env
TOMTOM_API_KEY=your_key_here
```
This enables live traffic monitoring and route-scoped incident checks.

### OSRM
Set:
```env
ROUTING_PROVIDER=osrm
OSRM_BASE_URL=https://router.project-osrm.org
```
OSRM can be used as a no-key fallback route provider if Google Maps is not configured.
Its request timeout defaults to 2.5 seconds (`OSRM_TIMEOUT_S`). In `auto` mode
this runs only after Google does not return routes.

## Troubleshooting

### Backend offline
The frontend may show a red backend offline banner if FastAPI is not running.
Fix:
```bash
cd geoagentic/backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

### Port already in use
If 5173 is busy, Vite will move to another port such as 5174.
Use the URL shown in the terminal.

### Routes look like curves instead of road-following lines
This usually means the routing API is unavailable or disabled.
The app will fallback automatically to its mock route generator.

### Place search returns limited results
The app includes built-in place data and can fall back to offline results if the online geocoder is not available.

### Duplicate vehicle names
Vehicle callsigns must be unique. Use a different callsign if the app reports a duplicate vehicle error.

## Validation / testing

Run backend tests:
```bash
cd geoagentic/backend
source .venv/bin/activate
pytest -q
```

This validates the route generation and agent behaviors in the offline test suite.

## Notes
- The demo is intended for local development and simulation, not production dispatch operations.
- The project is bounded to Bengaluru coordinates for consistency and demo realism.
- All external APIs are optional; the application remains functional without them.
