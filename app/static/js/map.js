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

    const tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors",
    });
    tiles.on("tileerror", () => {
        setStatus("Map tiles unavailable (offline / blocked network). Markers still work.");
    });
    tiles.addTo(map);

    const clusterGroup = L.markerClusterGroup({
        showCoverageOnHover: false,
        maxClusterRadius: 55,
    });
    map.addLayer(clusterGroup);

    let wardLayer = null;
    let clickLatLng = null;
    let clickMarker = null;
    let radiusCircle = null;
    let highlightMarker = null;

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

    const escapeHtml = (value) =>
        String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;");

    const popupHtml = (props) => {
        const address = props.address || props.location_name || "—";
        const thumb = props.thumbnail_url
            ? `<div class="map-popup-thumb"><img src="${escapeHtml(props.thumbnail_url)}" alt="capture"></div>`
            : "";
        const reviewLink = props.inspection_id
            ? `<a href="/inspections/${escapeHtml(props.inspection_id)}">Open inspection</a>
               · <a href="/repairs/verify?inspection_id=${escapeHtml(props.inspection_id)}">Before image</a>`
            : "";
        const rows = [
            ["Inspection", props.inspection_code || props.inspection_id || "—"],
            ["Address", address],
            ["Damage", props.damage_type || "—"],
            ["Severity", props.severity || "—"],
            ["Priority", props.priority || "—"],
            ["Action", props.recommended_action || "—"],
            ["Ward", props.ward_name || "—"],
            ["Status", props.status || "—"],
            ["Time", props.timestamp || "—"],
        ];

        return (
            thumb +
            rows
                .map(
                    ([label, value]) =>
                        `<strong>${escapeHtml(label)}:</strong> ${escapeHtml(value)}<br>`
                )
                .join("") +
            (reviewLink ? `<div class="mt-2">${reviewLink}</div>` : "") +
            `<div class="small text-muted mt-1">Relative depth is never shown as metric.</div>`
        );
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
        marker._roadProps = props;
        marker.on("click", () => {
            window.RoadInspectionMap && window.RoadInspectionMap.showBeforeImage(props);
        });
        return marker;
    };

    const renderFeatures = (collection, options = {}) => {
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

        if (options.focusLat != null && options.focusLng != null) {
            map.setView([options.focusLat, options.focusLng], options.zoom || 17);
            if (highlightMarker) {
                map.removeLayer(highlightMarker);
            }
            highlightMarker = L.circleMarker([options.focusLat, options.focusLng], {
                radius: 14,
                color: "#111827",
                weight: 2,
                fillColor: "#fbbf24",
                fillOpacity: 0.35,
            }).addTo(map);
        } else if (bounds.length && !options.skipFit) {
            map.fitBounds(bounds, { padding: [36, 36], maxZoom: 16 });
        }

        const mode = collection && collection.mode ? ` [${collection.mode}]` : "";
        setStatus(`${features.length} map feature(s) loaded${mode}`);
        return features;
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

    const loadFeatures = async (filters = {}, options = {}) => {
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
            renderFeatures(data, options);
            return data;
        } catch (error) {
            console.error(error);
            setStatus("Could not load map data. Check API / login session.");
            return null;
        }
    };

    const plotFeature = (feature, { pan = true } = {}) => {
        if (!feature || feature.geometry?.type !== "Point") {
            return null;
        }
        const marker = buildCircle(feature);
        if (!marker) {
            return null;
        }
        clusterGroup.addLayer(marker);
        const ll = marker.getLatLng();
        if (pan) {
            map.setView(ll, 17);
            marker.openPopup();
        }
        setStatus("Plotted latest capture on map");
        return marker;
    };

    const consumeLatestCaptureFocus = async () => {
        try {
            const raw = sessionStorage.getItem("roadInspectionLatestCapture");
            if (!raw) {
                return;
            }
            sessionStorage.removeItem("roadInspectionLatestCapture");
            const latest = JSON.parse(raw);
            const lat = Number(latest.lat);
            const lng = Number(latest.lng);
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
                return;
            }
            await loadFeatures({}, { focusLat: lat, focusLng: lng, zoom: 17, skipFit: true });
            setStatus(
                `Focused latest capture @ ${lat.toFixed(5)}, ${lng.toFixed(5)}` +
                    (latest.severity ? ` (${latest.severity})` : "")
            );
        } catch (error) {
            console.warn("Could not focus latest capture", error);
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

            if (!(data.features || []).length) {
                setStatus(data.note || "No ward boundaries available offline");
            } else {
                setStatus("Ward boundaries visible");
            }
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
            renderFeatures(data, { skipFit: true });
            if (radiusCircle) {
                map.removeLayer(radiusCircle);
            }
            radiusCircle = L.circle(clickLatLng, {
                radius: Number(radiusMeters) || 250,
                color: "#0f766e",
                weight: 1,
                fillOpacity: 0.08,
            }).addTo(map);
            const mode = data.mode ? ` (${data.mode})` : "";
            setStatus(`${(data.features || []).length} nearby feature(s)${mode}`);
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

    const showBeforeImage = async (props) => {
        const img = document.getElementById("map-before-img");
        const placeholder = document.getElementById("map-before-placeholder");
        const caption = document.getElementById("map-before-caption");
        const inspectionId = props && props.inspection_id;
        if (caption) {
            caption.textContent = props.location_name || props.address || "Selected location";
        }
        if (!inspectionId) {
            if (placeholder) {
                placeholder.classList.remove("d-none");
                placeholder.textContent = "No before image available for this location";
            }
            if (img) {
                img.classList.add("d-none");
            }
            return;
        }
        if (placeholder) {
            placeholder.classList.remove("d-none");
            placeholder.textContent = "Loading before image…";
        }
        try {
            const response = await fetch(`/api/inspections/${inspectionId}/before-image`, {
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            const data = await response.json();
            if (!response.ok || !data.url) {
                throw new Error(data.error || "missing");
            }
            if (img) {
                img.src = data.url;
                img.classList.remove("d-none");
            }
            if (placeholder) {
                placeholder.classList.add("d-none");
            }
        } catch (error) {
            if (img) {
                img.classList.add("d-none");
            }
            if (placeholder) {
                placeholder.classList.remove("d-none");
                placeholder.textContent = "No before image available for this location";
            }
        }
    };

    window.RoadInspectionMap = {
        loadFeatures,
        radiusSearch,
        toggleWardBoundaries,
        plotFeature,
        showBeforeImage,
        map,
    };

    loadFeatures().then(() => consumeLatestCaptureFocus());
    setTimeout(() => map.invalidateSize(), 200);
})();
