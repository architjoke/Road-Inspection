(() => {
    const mapEl = document.getElementById("inspection-map");
    if (!mapEl || typeof L === "undefined") {
        return;
    }

    const statusEl = document.getElementById("map-status");
    const geojsonUrl = mapEl.dataset.geojsonUrl;
    const wardsUrl = mapEl.dataset.wardsUrl;
    const radiusUrl = mapEl.dataset.radiusUrl;

    const severityColors = {
        SEVERE: "#dc2626",
        MODERATE: "#ea580c",
        MINOR: "#16a34a",
    };

    const map = L.map(mapEl, {
        zoomControl: true,
        scrollWheelZoom: true,
    }).setView([20.5937, 78.9629], 5);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    const clusterGroup = L.markerClusterGroup({
        showCoverageOnHover: false,
        maxClusterRadius: 55,
    });
    map.addLayer(clusterGroup);

    let wardLayer = null;
    let clickLatLng = null;
    let clickMarker = null;

    const setStatus = (message) => {
        if (statusEl) {
            statusEl.textContent = message;
        }
    };

    const colorForSeverity = (severity) => {
        if (!severity) {
            return "#2563eb";
        }
        return severityColors[String(severity).toUpperCase()] || "#2563eb";
    };

    const popupHtml = (props) => {
        const rows = [
            ["Inspection", props.inspection_code || props.inspection_id || "—"],
            ["Damage", props.damage_type || "—"],
            ["Severity", props.severity || "—"],
            ["Priority", props.priority || "—"],
            ["Ward", props.ward_name || "—"],
            ["Status", props.status || "—"],
            ["Time", props.timestamp || "—"],
        ];

        return rows
            .map(([label, value]) => `<strong>${label}:</strong> ${value}<br>`)
            .join("");
    };

    const buildCircle = (feature) => {
        const coords = feature.geometry && feature.geometry.coordinates;
        if (!coords || coords.length < 2) {
            return null;
        }

        const lng = coords[0];
        const lat = coords[1];
        const props = feature.properties || {};
        const marker = L.circleMarker([lat, lng], {
            radius: 8,
            color: "#ffffff",
            weight: 1.5,
            fillColor: colorForSeverity(props.severity),
            fillOpacity: 0.9,
        });

        marker.bindPopup(popupHtml(props));
        return marker;
    };

    const renderFeatures = (collection) => {
        clusterGroup.clearLayers();
        const features = (collection && collection.features) || [];
        const bounds = [];

        features.forEach((feature) => {
            if (!feature || feature.geometry?.type !== "Point") {
                return;
            }
            const marker = buildCircle(feature);
            if (!marker) {
                return;
            }
            clusterGroup.addLayer(marker);
            const ll = marker.getLatLng();
            bounds.push([ll.lat, ll.lng]);
        });

        if (bounds.length) {
            map.fitBounds(bounds, { padding: [36, 36], maxZoom: 16 });
        }

        setStatus(`${features.length} map feature(s) loaded`);
    };

    const toQuery = (filters = {}) => {
        const params = new URLSearchParams();
        (filters.severity || []).forEach((value) => params.append("severity", value));
        (filters.priority || []).forEach((value) => params.append("priority", value));
        if (filters.ward_id) {
            params.set("ward_id", filters.ward_id);
        }
        if (filters.date_from) {
            params.set("date_from", filters.date_from);
        }
        if (filters.date_to) {
            params.set("date_to", filters.date_to);
        }
        const qs = params.toString();
        return qs ? `${geojsonUrl}?${qs}` : geojsonUrl;
    };

    const loadFeatures = async (filters = {}) => {
        setStatus("Loading map data…");
        try {
            const response = await fetch(toQuery(filters), {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) {
                throw new Error(`GeoJSON HTTP ${response.status}`);
            }
            const data = await response.json();
            renderFeatures(data);
        } catch (error) {
            console.error(error);
            setStatus("Could not load map data. Check API / login session.");
        }
    };

    const toggleWardBoundaries = async (visible) => {
        if (!visible) {
            if (wardLayer) {
                map.removeLayer(wardLayer);
            }
            return;
        }

        setStatus("Loading ward boundaries…");
        try {
            const response = await fetch(wardsUrl, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            const data = await response.json();

            if (wardLayer) {
                map.removeLayer(wardLayer);
            }

            wardLayer = L.geoJSON(data, {
                style: {
                    color: "#1d4ed8",
                    weight: 1.5,
                    fillColor: "#93c5fd",
                    fillOpacity: 0.15,
                },
                onEachFeature(feature, layer) {
                    const name = feature.properties?.ward_name || "Ward";
                    layer.bindPopup(`<strong>${name}</strong>`);
                },
            }).addTo(map);

            setStatus("Ward boundaries visible");
        } catch (error) {
            console.error(error);
            setStatus("Ward boundaries could not be loaded");
        }
    };

    const radiusSearch = async (radiusMeters) => {
        if (!clickLatLng) {
            setStatus("Click the map first to choose a center point.");
            return;
        }

        const url =
            `${radiusUrl}?lat=${encodeURIComponent(clickLatLng.lat)}` +
            `&lng=${encodeURIComponent(clickLatLng.lng)}` +
            `&radius=${encodeURIComponent(radiusMeters || 250)}`;

        setStatus(`Searching within ${radiusMeters} m…`);
        try {
            const response = await fetch(url, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            const data = await response.json();
            renderFeatures(data);
            L.circle(clickLatLng, {
                radius: Number(radiusMeters) || 250,
                color: "#0f766e",
                weight: 1,
                fillOpacity: 0.08,
            }).addTo(map);
        } catch (error) {
            console.error(error);
            setStatus("Radius search failed");
        }
    };

    map.on("click", (event) => {
        clickLatLng = event.latlng;
        if (clickMarker) {
            map.removeLayer(clickMarker);
        }
        clickMarker = L.marker(clickLatLng).addTo(map);
        setStatus(
            `Center set: ${clickLatLng.lat.toFixed(5)}, ${clickLatLng.lng.toFixed(5)}`
        );
    });

    window.RoadInspectionMap = {
        loadFeatures,
        radiusSearch,
        toggleWardBoundaries,
    };

    loadFeatures();
    setTimeout(() => map.invalidateSize(), 200);
})();
