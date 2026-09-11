function inspectionUploadForm() {
    return {
        dragging: false,
        fileName: "",
        fileSizeLabel: "",
        previewUrl: "",
        previewKind: "",
        latitude: "",
        longitude: "",
        accuracy: "",
        latDisplay: "—",
        lngDisplay: "—",
        accDisplay: "—",
        gpsTitle: "Location not captured",
        gpsMessage: "Click the button below and allow location permission.",
        gpsCardClass: "",
        showManual: false,
        manualOverride: false,
        manualLat: "",
        manualLng: "",
        submitting: false,

        onFileChange(event) {
            this.applyFile(event.target.files[0]);
        },

        onDrop(event) {
            this.dragging = false;
            const file = event.dataTransfer.files[0];
            if (!file) {
                return;
            }
            const input = document.getElementById("media_file");
            const transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            this.applyFile(file);
        },

        applyFile(file) {
            if (!file) {
                this.fileName = "";
                this.fileSizeLabel = "";
                this.previewUrl = "";
                return;
            }

            this.fileName = file.name;
            this.fileSizeLabel = `(${(file.size / (1024 * 1024)).toFixed(2)} MB)`;

            if (this.previewUrl) {
                URL.revokeObjectURL(this.previewUrl);
            }

            if (file.type.startsWith("image/")) {
                this.previewKind = "image";
                this.previewUrl = URL.createObjectURL(file);
            } else if (file.type.startsWith("video/")) {
                this.previewKind = "video";
                this.previewUrl = URL.createObjectURL(file);
            } else {
                this.previewKind = "";
                this.previewUrl = "";
            }
        },

        captureLocation() {
            if (!navigator.geolocation) {
                this.gpsCardClass = "gps-error";
                this.gpsTitle = "Geolocation is not supported";
                this.gpsMessage = "Use manual latitude/longitude fields instead.";
                this.showManual = true;
                return;
            }

            this.gpsCardClass = "";
            this.gpsTitle = "Capturing current location";
            this.gpsMessage = "Please allow browser location permission if requested.";

            navigator.geolocation.getCurrentPosition(
                (position) => {
                    this.latitude = position.coords.latitude.toFixed(8);
                    this.longitude = position.coords.longitude.toFixed(8);
                    this.accuracy = position.coords.accuracy.toFixed(2);
                    this.latDisplay = position.coords.latitude.toFixed(6);
                    this.lngDisplay = position.coords.longitude.toFixed(6);
                    this.accDisplay = `±${position.coords.accuracy.toFixed(1)} m`;
                    this.gpsCardClass = "gps-captured";
                    this.gpsTitle = "Location captured";
                    this.gpsMessage = "Browser GPS coordinates are ready for submission.";
                    this.manualOverride = false;
                    this.showManual = false;
                },
                (error) => {
                    const messages = {
                        1: "Location permission was denied.",
                        2: "Location could not be determined.",
                        3: "Location request timed out.",
                    };
                    this.gpsCardClass = "gps-error";
                    this.gpsTitle = "Location not captured";
                    this.gpsMessage =
                        (messages[error.code] || "Unable to capture location.") +
                        " Enter coordinates manually or rely on photo EXIF GPS.";
                    this.showManual = true;
                },
                {
                    enableHighAccuracy: true,
                    timeout: 15000,
                    maximumAge: 0,
                }
            );
        },

        syncManual() {
            this.manualOverride = true;
            this.latitude = this.manualLat;
            this.longitude = this.manualLng;
            this.latDisplay = this.manualLat || "—";
            this.lngDisplay = this.manualLng || "—";
            this.accDisplay = "manual";
            this.gpsCardClass = this.manualLat && this.manualLng ? "gps-captured" : "gps-error";
            this.gpsTitle = "Manual GPS override";
            this.gpsMessage = "Coordinates will be taken from the manual fields.";
        },

        onSubmit(event) {
            const input = document.getElementById("media_file");
            if (!input.files.length) {
                event.preventDefault();
                window.RoadInspectionUI?.toast("Please choose a road image or video file.", "danger");
                return;
            }

            // Allow submit without browser GPS — backend can use EXIF or manual fields.
            this.submitting = true;
        },
    };
}

// Keep legacy helpers available for non-Alpine pages.
document.addEventListener("DOMContentLoaded", () => {
    const locationButton = document.getElementById("capture-location-button");
    if (locationButton && !locationButton.hasAttribute("@click") && window.RoadInspectionGPS) {
        locationButton.addEventListener("click", () => {
            window.RoadInspectionGPS.captureLocation();
        });
    }
});
