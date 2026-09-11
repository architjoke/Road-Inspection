# Phase 11 Progress — Leaflet GIS & Camera Capture

## Completed
- [x] Audit (`docs/phase_11_audit.md`)
- [x] Offline map service + Haversine radius (`map_service.py`)
- [x] `GET /api/map/detections` GeoJSON (severity, priority, damage, thumbnail, timestamp)
- [x] Offline fallback for `/api/inspections/geojson` and `/api/inspections/radius`
- [x] Live camera capture UI (`getUserMedia`) + auto GPS + upload/analyze feedback
- [x] Leaflet live focus via `sessionStorage` after capture (`map.js`)
- [x] Severity-colored markers + richer popups (action, thumbnail, review link)
- [x] Tests (`test_map_api.py`)
- [x] README / progress docs

## Tests
```
Phase 11 map focused: 9 passed
Mock suite (YOLO_FORCE_MOCK=true): 94 passed, 6 skipped
Real YOLO CPU suite: 94 passed, 6 skipped
```

## Remaining limitations
- OSM tiles need network; markers still work offline with status note
- Ward polygons unavailable without PostGIS
- Camera requires browser permission / HTTPS in some environments
- In-memory map data is process-local

## RDS/Aurora
Not contacted.
