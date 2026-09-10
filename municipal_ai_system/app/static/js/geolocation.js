window.RoadInspectionGPS = {
    captureLocation() {
        const statusCard = document.getElementById("gps-status-card");
        const statusTitle = document.getElementById("gps-status-title");
        const statusMessage = document.getElementById("gps-status-message");

        const latitudeDisplay = document.getElementById("latitude-display");
        const longitudeDisplay = document.getElementById("longitude-display");
        const accuracyDisplay = document.getElementById("accuracy-display");

        const latitudeInput = document.getElementById("location_latitude");
        const longitudeInput = document.getElementById("location_longitude");
        const accuracyInput = document.getElementById("gps_accuracy_meters");

        if (!navigator.geolocation) {
            statusCard.classList.remove("gps-captured");
            statusCard.classList.add("gps-error");

            statusTitle.textContent = "Geolocation is not supported";
            statusMessage.textContent =
                "Your browser does not support current location capture.";

            return;
        }

        statusCard.classList.remove("gps-captured", "gps-error");

        statusTitle.textContent = "Capturing current location";
        statusMessage.textContent =
            "Please allow browser location permission if requested.";

        navigator.geolocation.getCurrentPosition(
            (position) => {
                const latitude = position.coords.latitude;
                const longitude = position.coords.longitude;
                const accuracy = position.coords.accuracy;

                latitudeInput.value = latitude.toFixed(8);
                longitudeInput.value = longitude.toFixed(8);
                accuracyInput.value = accuracy.toFixed(2);

                latitudeDisplay.textContent = latitude.toFixed(6);
                longitudeDisplay.textContent = longitude.toFixed(6);
                accuracyDisplay.textContent = `±${accuracy.toFixed(1)} m`;

                statusCard.classList.remove("gps-error");
                statusCard.classList.add("gps-captured");

                statusTitle.textContent = "Location captured";
                statusMessage.textContent =
                    "Current browser GPS coordinates are ready for submission.";
            },
            (error) => {
                const messages = {
                    1: "Location permission was denied. Allow location access and try again.",
                    2: "Your location could not be determined. Try again outdoors or check GPS.",
                    3: "Location request timed out. Please try again."
                };

                statusCard.classList.remove("gps-captured");
                statusCard.classList.add("gps-error");

                statusTitle.textContent = "Location not captured";
                statusMessage.textContent =
                    messages[error.code] || "Unable to capture location.";
            },
            {
                enableHighAccuracy: true,
                timeout: 15000,
                maximumAge: 0
            }
        );
    }
};