# AI Road Inspection System

Municipal road-damage inspection platform for Indian cities. Field workers
photograph roads; a YOLO model finds potholes and cracks and estimates their size,
severity and repair cost; municipal staff review the findings, assign repairs to
contractors and verify each repair with before/after photo comparison. Reports
export as PDF / Excel / CSV / JSON.

**Stack:** Flask 3 · Supabase Postgres + PostGIS · SQLAlchemy 2 · Jinja2 +
Bootstrap 5 + Alpine.js · Leaflet · ImageKit · Ultralytics YOLO · OpenCV.

## Project layout

Everything runs from this folder (the project root).

```text
.
├── app/                    Flask application
│   ├── routes/             pages + JSON API (blueprints)
│   ├── services/           business logic, persistence, access control
│   ├── ai/                 detection, size estimation, severity, cost, before/after verification
│   ├── workers/            background analysis jobs
│   ├── auth/ models/ utils/ templates/ static/
├── tests/                  unit/ · integration/ · e2e/
├── scripts/
│   ├── admin/              seed users, inspect schema, hash passwords, stamp migrations
│   └── training/           India dataset download/convert/build, train, evaluate + promote
├── migrations/             Alembic (baseline of the Supabase schema)
├── artifacts/ai_models/    deployed model: best.pt + best.model.json
├── data/                   datasets (git-ignored)
├── docs/                   detailed guides
├── instance/               runtime data: logs, uploads, reports, media cache (git-ignored)
├── .env.example            every setting, documented
├── requirements.txt        all Python dependencies
├── run.py · wsgi.py        dev server · production entry point
└── start.bat · start.sh    one-command start
```

## Getting started (Windows)

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`: `DATABASE_URL` (Supabase session pooler), `SECRET_KEY`, ImageKit keys.
Then:

```bat
start.bat
```

Open http://127.0.0.1:5000. Live camera and GPS need HTTPS outside localhost
(see [docs/operations.md](docs/operations.md)). Linux/macOS: `./start.sh`.

## Common commands (from the project root)

| Task | Command |
|---|---|
| Run the app | `start.bat` or `python run.py` |
| All tests (offline, mock model, ≈4 min) | `python -m pytest` |
| Fast tests only | `python -m pytest tests/unit` |
| Production server (Linux) | `gunicorn -w 2 -b 127.0.0.1:5000 wsgi:app` |
| Create the admin account | `python scripts/admin/seed_auth.py` |
| Inspect the database schema | `python scripts/admin/inspect_schema.py` |
| Download + convert India training data | `python scripts/training/fetch_rdd2022_india.py` then `python -I scripts/training/convert_rdd_xml.py` |
| Build training set / train / evaluate | `python scripts/training/build_dataset.py` · `train.py --profile laptop` · `evaluate.py --candidate … --promote` |
| Package data + notebook for Kaggle's free GPU | `python scripts/training/package_kaggle.py` (steps: docs/ml/retraining.md) |

## Current status (2026-11-21)

| Area | State |
|---|---|
| Workflow: capture → analysis → review → repair → verify → report | working; detections, reviews and repair jobs stored in Supabase |
| Access control | per role and per municipality |
| GPS | map pin → photo/video GPS → browser; PC Wi-Fi locations flagged or refused; map picker on upload |
| Wards | boundary editor; inspections assigned by polygon — **draw your ward boundaries** |
| Severity / cost | severity from estimated physical size; cost = quantity × unit rate — **load your Schedule of Rates** |
| Before/after verification | damage area vs surrounding road, local alignment; auto-verify only when nothing is flagged |
| Detection model | **retraining required** — current weights learnt potholes from mislabelled data (mAP50 0.43); corrected India data and recipe are ready |

## Documentation

| Guide | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | components, code layout, persistence, access control, runtime data |
| [docs/workflow.md](docs/workflow.md) | roles, end-to-end flow, statuses, pages and APIs |
| [docs/ai-pipeline.md](docs/ai-pipeline.md) | detection, size, severity, cost, verification, model files |
| [docs/gps-and-wards.md](docs/gps-and-wards.md) | GPS trust order, PC uploads, ward boundaries, image quality gate |
| [docs/operations.md](docs/operations.md) | setup, HTTPS, administration, tests, troubleshooting |
| [docs/ml/retraining.md](docs/ml/retraining.md) | India-only training recipe, evaluation gate, crack-outline model |
| [docs/data.md](docs/data.md) | datasets on disk and how they were produced |
| [docs/history/](docs/history/) | original phase-by-phase build brief (historical) |

## Data and licences

Training data is India-only: official RDD2022 India annotations (CC BY-SA 4.0,
figshare 10.6084/m9.figshare.21431547) and road-pavement crack masks
(CRACK500, GAPS384, CrackTree200, CFD, AEL). Credit the sources and share derived
datasets under the same licence.
