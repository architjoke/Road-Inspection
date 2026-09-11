# Phase 11 Audit — Leaflet GIS Map & Real-time Camera Capture

**Date:** 2026-09-11  
**RDS/Aurora connectivity was not checked and is not required.**

## Existing GIS / mapping

| Capability | Status |
|------------|--------|
| Leaflet map `/map` | Present (`map/index.html` + `static/js/map.js`) |
| OSM tiles + MarkerCluster | Present |
| Severity color markers | SEVERE red / MODERATE orange / MINOR green |
| GeoJSON API | `/api/inspections/geojson` (PostGIS) |
| Radius API | `/api/inspections/radius` (PostGIS `ST_DWithin`) |
| Ward boundaries | `/api/wards/boundaries` (PostGIS) |
| `/api/map/detections` | **Missing** |
| Offline GeoJSON / Haversine | **Missing** |

## Camera / capture

| Capability | Status |
|------------|--------|
| File upload + drag-drop | Present |
| Browser geolocation | Present |
| EXIF GPS | Present (`gps_service.py`) |
| Live `getUserMedia` camera | **Missing** |
| One-click capture + auto geotag → analyze | **Missing** |

## PostGIS spatial queries

- Online: `ST_AsGeoJSON`, `ST_DWithin` in `geo_utils.py`
- Offline/TESTING: map APIs currently fail or empty — need memory/Haversine fallback

## Files to create / modify

**Create:** offline map builders, Haversine helper, camera JS, Phase 11 tests, progress docs  
**Modify:** `api.py`, `geo_utils.py`, `map.js`, `map/index.html`, `inspections/new.html`, upload JS, `offline_store.py`, README

## Test command

```bash
cd municipal_ai_system
set OFFLINE_MODE=true
set YOLO_FORCE_MOCK=true
python -m pytest app/tests/test_map_api.py app/tests/test_camera_capture.py -q
python -m pytest app/tests -q
```
