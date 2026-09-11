"""Detection model placeholder — persistence uses detection_service + reflection."""

# Declarative Detection ORM is intentionally not duplicated here.
# Live schema is reflected into `get_model("detections")` when DB is available.
# Offline tests use `app.services.detection_service` in-memory store.
