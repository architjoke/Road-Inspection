# GPS and wards

## Where an inspection is

`app/services/gps_service.py::resolve_inspection_gps_details` decides, in order of trust:

1. **Map pin / typed coordinates** (`gps_manual_override`) — what an office user does on a PC.
2. **GPS stored in the file** — photo EXIF (with `GPSHPositioningError` as accuracy)
   or video ISO 6709 metadata (Android `©xyz`, iPhone QuickTime location).
3. **Browser geolocation** — reliable on a phone outdoors; on a PC it is a Wi-Fi/IP
   estimate (often the office):
   * worse than ±100 m → saved but flagged for GPS review (`GPS_REVIEW_ACCURACY_M`)
   * worse than ±5 km → refused, user must pin the map (`GPS_REJECT_ACCURACY_M`)

Coordinates outside India are flagged. The reasons are stored in
`inspection_media.exif_data.gps_review_reasons` and shown in the inspection notes.

The upload page reads photo GPS in the browser as soon as a file is chosen, warns
that **WhatsApp removes photo GPS** (send as *document* to keep it), and opens a
click-to-pin map whenever the location is missing or imprecise.

## Wards

* Each ward needs a boundary polygon (`wards.ward_boundary`, SRID 4326). Set it on
  **Wards → Edit**: click the map to add corners, or load/paste GeoJSON
  (Polygon, MultiPolygon, Feature, FeatureCollection — the largest polygon is used).
* Saving a boundary computes its area and assigns that ward to existing
  inspections inside it (and their detections) that had none.
* New inspections get their ward by `ST_Contains(ward_boundary, point)`.

The wards list shows which wards still have no boundary — inspections in those
areas stay unassigned until one is drawn.

## Image quality gate

Uploads under 480 px on the short side are rejected (useless for detection);
under 720 px are accepted with a warning. Recommended 1280×720 or larger.
