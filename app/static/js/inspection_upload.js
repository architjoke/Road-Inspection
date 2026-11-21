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
        photoGps: null,
        photoGpsChecked: false,
        picker: null,
        reviewAccuracyM: 100,

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

            this.checkPhotoGps(file);

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

        async checkPhotoGps(file) {
            this.photoGps = null;
            this.photoGpsChecked = false;
            if (!file || !file.type.startsWith("image/") || !window.readPhotoGps) {
                return;
            }
            const gps = await window.readPhotoGps(file);
            this.photoGpsChecked = true;
            if (gps) {
                this.photoGps = gps;
                if (!this.manualOverride) {
                    this.latDisplay = gps.lat.toFixed(6);
                    this.lngDisplay = gps.lng.toFixed(6);
                    this.accDisplay = "from photo";
                    this.gpsCardClass = "gps-captured";
                    this.gpsTitle = "Photo GPS found";
                    this.gpsMessage =
                        "The location stored in this photo will be used (it beats browser location).";
                    this.fillAddressFromGps(gps.lat, gps.lng);
                }
                if (this.picker) this.picker.setPoint(gps.lat, gps.lng);
            } else if (!this.manualOverride) {
                const whatsapp = /whatsapp|^img-\d{8}-wa/i.test(file.name);
                this.gpsCardClass = "gps-error";
                this.gpsTitle = "This photo has no GPS";
                this.gpsMessage = whatsapp
                    ? "WhatsApp removes photo location. Pin the damage on the map below."
                    : "Pin the damage on the map below, or capture location on a phone at the site.";
                this.openManual();
            }
        },

        openManual() {
            this.showManual = true;
            this.$nextTick(() => this.mountPicker());
        },

        mountPicker() {
            if (this.picker) {
                this.picker.map.invalidateSize();
                return;
            }
            if (!window.LocationPicker) {
                return;
            }
            const el = document.getElementById("gps-pick-map");
            const start = this.photoGps
                || (this.latitude && this.longitude
                    ? { lat: parseFloat(this.latitude), lng: parseFloat(this.longitude) }
                    : null);
            this.picker = window.LocationPicker.mount(el, {
                lat: start ? start.lat : NaN,
                lng: start ? start.lng : NaN,
                showMarker: Boolean(this.photoGps),
                onPick: (lat, lng) => {
                    this.manualLat = lat.toFixed(8);
                    this.manualLng = lng.toFixed(8);
                    this.syncManual();
                },
            });
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
                this.gpsMessage = "Pin the damage on the map instead.";
                this.openManual();
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
                    this.manualOverride = false;
                    const accuracy = position.coords.accuracy;
                    if (this.photoGps) {
                        this.gpsCardClass = "gps-captured";
                        this.gpsTitle = "Photo GPS will be used";
                        this.gpsMessage =
                            "This photo carries its own location, which is more reliable than browser location.";
                        return;
                    }
                    if (accuracy > this.reviewAccuracyM) {
                        // PC / Wi-Fi estimate: probably the office, not the damage.
                        this.gpsCardClass = "gps-error";
                        this.gpsTitle = "Approximate location only (±" + Math.round(accuracy) + " m)";
                        this.gpsMessage =
                            "This looks like a PC/Wi-Fi estimate, not the damage site. Pin the exact spot on the map.";
                        const lat = position.coords.latitude;
                        const lng = position.coords.longitude;
                        this.openManual();
                        this.$nextTick(() => {
                            if (this.picker) this.picker.setPoint(lat, lng, 14);
                        });
                        return;
                    }
                    this.gpsCardClass = "gps-captured";
                    this.gpsTitle = "Location captured";
                    this.gpsMessage = "Resolving full street address…";
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
                        " Pin the damage on the map, or upload an original photo with GPS.";
                    this.openManual();
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
            this.gpsTitle = "Location pinned manually";
            this.gpsMessage = "The pinned / typed coordinates will be used for this inspection.";
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
