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
        resolvingAddress: false,

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

        async fillAddressFromGps(lat, lng) {
            const locationInput = document.getElementById("location_name");
            if (!locationInput || !lat || !lng) {
                return;
            }

            this.resolvingAddress = true;
            try {
                const response = await fetch(
                    `/api/geocode/reverse?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}`,
                    { credentials: "same-origin", headers: { Accept: "application/json" } }
                );
                if (!response.ok) {
                    return;
                }
                const data = await response.json();
                const address = data.address || data.display_name;
                if (!address) {
                    return;
                }

                const current = (locationInput.value || "").trim();
                const placeholders = ["", "field upload", "road inspection", "location"];
                const line = data.location_line || data.address || data.display_name;
                if (!line) {
                    return;
                }
                if (!current || placeholders.includes(current.toLowerCase())) {
                    locationInput.value = line.replace(/^Location:\s*/i, "");
                } else if (!current.includes(data.address || "")) {
                    locationInput.value = `${current} — ${data.address}`;
                }

                window.RoadInspectionUI?.toast(line, "success");
            } catch (error) {
                console.warn("Address lookup failed", error);
            } finally {
                this.resolvingAddress = false;
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
                    this.gpsMessage = "Resolving full street address…";
                    this.manualOverride = false;
                    this.showManual = false;
                    this.fillAddressFromGps(this.latitude, this.longitude).then(() => {
                        this.gpsMessage = "GPS and street address are ready for submission.";
                    });
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
            if (this.manualLat && this.manualLng) {
                this.fillAddressFromGps(this.manualLat, this.manualLng);
            }
        },

        onSubmit(event) {
            const input = document.getElementById("media_file");
            if (!input.files.length) {
                event.preventDefault();
                window.RoadInspectionUI?.toast("Please choose a road image or video file.", "danger");
                return;
            }

            this.submitting = true;
        },
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const locationButton = document.getElementById("capture-location-button");
    if (locationButton && !locationButton.hasAttribute("@click") && window.RoadInspectionGPS) {
        locationButton.addEventListener("click", () => {
            window.RoadInspectionGPS.captureLocation();
        });
    }
});
