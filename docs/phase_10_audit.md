# Phase 10 Audit — Damage Severity Engine

**Date:** 2026-09-11  
**RDS/Aurora connectivity was not checked and is not required.**

## Existing severity behavior

| Item | Current state |
|------|----------------|
| Levels | `MINOR` / `MODERATE` / `SEVERE` (map, dashboard, annotator) |
| Priority | `SEVERE→1`, `MODERATE→2`, `MINOR→3` |
| Logic location | Hardcoded in `detection_postprocessor.infer_severity` |
| `severity_engine.py` | Thin re-export only |
| Repair recommendations | **Not produced** by AI severity path |
| Manual-review flag | Inspection GPS review only; not severity-driven |
| Config | File/env for YOLO/seg/depth; **no severity thresholds in config** |
| DB thresholds | **Not available** offline; reflected detections store severity/priority strings when online |

### Current thresholds (hardcoded)

| Class | Rules |
|-------|--------|
| Pothole | area%≥5 or rel_depth≥0.75 → SEVERE; else conf≥0.35 → SEVERE else MODERATE |
| Alligator | area%≥8 → SEVERE; else conf≥0.45 → SEVERE else MODERATE |
| Crack | conf≥0.6 or area%≥3 → MODERATE else MINOR |
| Other | conf≥0.7 → MODERATE else MINOR |

### Fields already produced

Per detection: `severity`, `priority`, `status=PENDING_REVIEW`, `severity_evidence` (feature bag).  
Phase 8/9 optionally attach `mask_pixel_count`, `damaged_area_percentage`, relative depth stats.

## Reusable code

- `app/ai/detection_postprocessor.py` — enrich / attach seg+depth
- `app/ai/severity_engine.py` — expand into real engine
- `app/workers/inspection_worker.py` — post-detect enrichment points
- `app/services/detection_service.py` — memory persistence
- Map/dashboard already expect MINOR/MODERATE/SEVERE + priority 1–3

## Missing requirements

1. Configurable rule repository (single source of thresholds)
2. Hybrid score + explanation + signals used/unavailable
3. Recommended action + repair type
4. Manual-review conditions + conflict detection
5. Explicit bbox-area fallback when segmentation missing
6. Relative depth as supporting evidence only (never metric)
7. Inspection-level severity summary in task result
8. Persistence of new severity fields in memory store
9. Dedicated Phase 10 tests

## Files to create / modify

**Create:** `app/ai/severity_rules.py`, `app/tests/test_severity_engine.py`, `docs/phase_10_progress.md`  
**Expand:** `severity_engine.py`, `detection_postprocessor.py`, `config.py`, worker summary, `detection_service.py`  
**Docs:** README, `.env.example`, this audit

## Test command

```bash
cd municipal_ai_system
set OFFLINE_MODE=true
set YOLO_FORCE_MOCK=true
python -m pytest app/tests/test_severity_engine.py -q
python -m pytest app/tests -q
```
