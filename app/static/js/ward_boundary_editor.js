/* Click-to-draw ward polygon editor that keeps a GeoJSON textarea in sync. */
document.addEventListener("DOMContentLoaded", () => {
    const mapEl = document.getElementById("boundary-map");
    const textarea = document.getElementById("boundary_geojson");
    const status = document.getElementById("boundary-status");
    if (!mapEl || !textarea || typeof L === "undefined") return;

    const map = L.map(mapEl).setView([20.5937, 78.9629], 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    let points = []; // [lat, lng]
    let shape = null;
    let markers = [];

    function redraw(fit) {
        if (shape) map.removeLayer(shape);
        markers.forEach((m) => map.removeLayer(m));
        markers = points.map((p) => L.circleMarker(p, { radius: 4, color: "#1d4ed8" }).addTo(map));
        shape = points.length >= 3
            ? L.polygon(points, { color: "#1d4ed8", weight: 2, fillOpacity: 0.15 }).addTo(map)
            : (points.length === 2 ? L.polyline(points, { color: "#1d4ed8" }).addTo(map) : null);
        if (fit && shape) map.fitBounds(shape.getBounds(), { padding: [20, 20] });
        status.textContent = points.length >= 3
            ? `${points.length} corner points`
            : `${points.length} point(s) — add at least 3`;
    }

    function writeGeoJson() {
        if (points.length < 3) {
            textarea.value = "";
            return;
        }
        const ring = points.map(([lat, lng]) => [Number(lng.toFixed(7)), Number(lat.toFixed(7))]);
        ring.push(ring[0]);
        textarea.value = JSON.stringify({ type: "Polygon", coordinates: [ring] });
    }

    function polygonFromGeoJson(data) {
        const polys = [];
        const walk = (obj) => {
            if (!obj || typeof obj !== "object") return;
            if (obj.type === "FeatureCollection") (obj.features || []).forEach(walk);
            else if (obj.type === "Feature") walk(obj.geometry);
            else if (obj.type === "Polygon") polys.push(obj.coordinates);
            else if (obj.type === "MultiPolygon") (obj.coordinates || []).forEach((c) => polys.push(c));
        };
        walk(data);
        if (!polys.length) return null;
        polys.sort((a, b) => b[0].length - a[0].length);
        const ring = polys[0][0].map(([lng, lat]) => [lat, lng]);
        if (ring.length > 1) {
            const [a, b] = [ring[0], ring[ring.length - 1]];
            if (a[0] === b[0] && a[1] === b[1]) ring.pop();
        }
        return ring;
    }

    function loadFromText(fit) {
        const text = textarea.value.trim();
        if (!text) {
            points = [];
            redraw(false);
            return;
        }
        try {
            const ring = polygonFromGeoJson(JSON.parse(text));
            if (!ring) throw new Error("no polygon");
            points = ring;
            redraw(fit);
        } catch (error) {
            status.textContent = "GeoJSON could not be read — check the format.";
        }
    }

    map.on("click", (event) => {
        points.push([event.latlng.lat, event.latlng.lng]);
        redraw(false);
        writeGeoJson();
    });
    document.getElementById("boundary-undo").addEventListener("click", () => {
        points.pop();
        redraw(false);
        writeGeoJson();
    });
    document.getElementById("boundary-clear").addEventListener("click", () => {
        points = [];
        redraw(false);
        writeGeoJson();
    });
    document.getElementById("boundary-file").addEventListener("change", async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        textarea.value = await file.text();
        loadFromText(true);
    });
    textarea.addEventListener("change", () => loadFromText(true));

    loadFromText(true);
    if (!points.length && navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
            (pos) => map.setView([pos.coords.latitude, pos.coords.longitude], 13),
            () => {},
            { timeout: 5000 }
        );
    }
    setTimeout(() => map.invalidateSize(), 50);
});
