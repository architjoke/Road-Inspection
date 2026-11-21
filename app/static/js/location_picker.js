/*
 * Location helpers for uploads from a PC.
 *
 * readPhotoGps(file)  -> Promise<{lat, lng} | null>
 *   Reads GPS from JPEG EXIF in the browser so the user sees immediately
 *   whether the photo carries its own location (WhatsApp copies do not).
 *
 * LocationPicker.mount(el, {lat, lng, onPick})
 *   Leaflet map; clicking (or dragging the pin) reports the chosen point.
 */
(function () {
    function readRational(view, offset, little) {
        const num = view.getUint32(offset, little);
        const den = view.getUint32(offset + 4, little);
        return den ? num / den : 0;
    }

    function parseExifGps(buffer) {
        const view = new DataView(buffer);
        if (view.byteLength < 4 || view.getUint16(0) !== 0xffd8) return null; // not JPEG
        let offset = 2;
        while (offset + 4 < view.byteLength) {
            const marker = view.getUint16(offset);
            const size = view.getUint16(offset + 2);
            if (marker === 0xffe1 && view.getUint32(offset + 4) === 0x45786966) { // "Exif"
                const tiff = offset + 10;
                const little = view.getUint16(tiff) === 0x4949;
                const ifd0 = tiff + view.getUint32(tiff + 4, little);
                const entries = view.getUint16(ifd0, little);
                let gpsIfd = null;
                for (let i = 0; i < entries; i++) {
                    const entry = ifd0 + 2 + i * 12;
                    if (view.getUint16(entry, little) === 0x8825) {
                        gpsIfd = tiff + view.getUint32(entry + 8, little);
                    }
                }
                if (gpsIfd === null) return null;
                const tags = {};
                const count = view.getUint16(gpsIfd, little);
                for (let i = 0; i < count; i++) {
                    const entry = gpsIfd + 2 + i * 12;
                    const tag = view.getUint16(entry, little);
                    if (tag === 1 || tag === 3) {
                        tags[tag] = String.fromCharCode(view.getUint8(entry + 8));
                    } else if (tag === 2 || tag === 4) {
                        const valueOffset = tiff + view.getUint32(entry + 8, little);
                        tags[tag] = [0, 1, 2].map((k) => readRational(view, valueOffset + k * 8, little));
                    }
                }
                if (!tags[2] || !tags[4]) return null;
                let lat = tags[2][0] + tags[2][1] / 60 + tags[2][2] / 3600;
                let lng = tags[4][0] + tags[4][1] / 60 + tags[4][2] / 3600;
                if (tags[1] === "S") lat = -lat;
                if (tags[3] === "W") lng = -lng;
                if (!lat && !lng) return null;
                return { lat, lng };
            }
            if ((marker & 0xff00) !== 0xff00) break;
            offset += 2 + size;
        }
        return null;
    }

    window.readPhotoGps = async function (file) {
        if (!file || !/jpe?g$/i.test(file.type.split("/")[1] || "")) return null;
        try {
            const head = await file.slice(0, 256 * 1024).arrayBuffer();
            return parseExifGps(head);
        } catch (error) {
            console.warn("EXIF GPS read failed", error);
            return null;
        }
    };

    window.LocationPicker = {
        mount(element, options) {
            if (!element || typeof L === "undefined") return null;
            const opts = options || {};
            const hasPoint = Number.isFinite(opts.lat) && Number.isFinite(opts.lng);
            const map = L.map(element).setView(
                hasPoint ? [opts.lat, opts.lng] : [20.5937, 78.9629],
                hasPoint ? 16 : 5
            );
            L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
                maxZoom: 19,
                attribution: "&copy; OpenStreetMap contributors",
            }).addTo(map);

            let marker = null;
            const place = (latlng, notify) => {
                if (!marker) {
                    marker = L.marker(latlng, { draggable: true }).addTo(map);
                    marker.on("dragend", () => place(marker.getLatLng(), true));
                } else {
                    marker.setLatLng(latlng);
                }
                if (notify && typeof opts.onPick === "function") {
                    opts.onPick(latlng.lat, latlng.lng);
                }
            };
            if (hasPoint && opts.showMarker) place(L.latLng(opts.lat, opts.lng), false);
            map.on("click", (event) => place(event.latlng, true));
            setTimeout(() => map.invalidateSize(), 50);
            return {
                map,
                setPoint(lat, lng, zoom) {
                    place(L.latLng(lat, lng), false);
                    map.setView([lat, lng], zoom || Math.max(map.getZoom(), 16));
                },
            };
        },
    };
})();
