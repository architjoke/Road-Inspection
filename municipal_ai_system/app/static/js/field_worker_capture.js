(() => {
    const root = document.getElementById("live-capture");
    if (!root) {
        return;
    }

    const video = document.getElementById("fw-video");
    const canvas = document.getElementById("fw-canvas");
    const preview = document.getElementById("fw-preview");
    const gpsEl = document.getElementById("fw-gps-status");
    const coordsEl = document.getElementById("fw-coords");
    const messageEl = document.getElementById("fw-message");
    const progressEl = document.getElementById("fw-progress");
    const startBtn = document.getElementById("fw-start");
    const shutterBtn = document.getElementById("fw-shutter");
    const retakeBtn = document.getElementById("fw-retake");
    const uploadBtn = document.getElementById("fw-upload");
    const closeBtn = document.getElementById("fw-close");
    const livePanel = document.getElementById("fw-live-panel");
    const previewPanel = document.getElementById("fw-preview-panel");
    const iosNote = document.getElementById("fw-ios-note");
    const httpsNote = document.getElementById("fw-https-note");

    const idleHint = document.getElementById("fw-idle-hint");
    const previewGpsEl = document.getElementById("fw-preview-gps");

    let stream = null;
    let watchId = null;
    let blob = null;
    let gps = { lat: null, lng: null, accuracy: null, status: "idle" };
    let uploading = false;

    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

    const csrfToken = () =>
        document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
        document.querySelector('input[name="csrf_token"]')?.value ||
        "";

    const setIdle = (idle) => {
        if (idleHint) {
            idleHint.classList.toggle("d-none", !idle);
        }
    };

    const setGpsStatus = (text, kind) => {
        if (!gpsEl) {
            return;
        }
        gpsEl.textContent = text;
        gpsEl.classList.remove("is-ok", "is-warn", "is-err");
        if (kind) {
            gpsEl.classList.add(kind);
        }
        if (previewGpsEl) {
            previewGpsEl.textContent = text;
            previewGpsEl.classList.remove("is-ok", "is-warn", "is-err");
            if (kind) {
                previewGpsEl.classList.add(kind);
            }
        }
    };

    const setMessage = (text, kind) => {
        if (!messageEl) {
            return;
        }
        messageEl.className = `alert mt-3 ${kind || "alert-info"}`;
        messageEl.textContent = text;
        messageEl.classList.toggle("d-none", !text);
    };

    const showPreview = (url) => {
        setIdle(false);
        livePanel.classList.add("d-none");
        previewPanel.classList.remove("d-none");
        preview.src = url;
        shutterBtn.disabled = true;
        retakeBtn.disabled = false;
        uploadBtn.disabled = gps.lat == null;
    };

    const showLive = () => {
        setIdle(false);
        previewPanel.classList.add("d-none");
        livePanel.classList.remove("d-none");
        if (preview.src) {
            URL.revokeObjectURL(preview.src);
        }
        preview.removeAttribute("src");
        blob = null;
        shutterBtn.disabled = !stream;
        retakeBtn.disabled = true;
        uploadBtn.disabled = true;
    };

    const resetIdleUi = () => {
        stopCamera();
        previewPanel.classList.add("d-none");
        livePanel.classList.add("d-none");
        if (preview.src) {
            URL.revokeObjectURL(preview.src);
        }
        preview.removeAttribute("src");
        blob = null;
        gps = { lat: null, lng: null, accuracy: null, status: "idle" };
        setGpsStatus("GPS idle — start the camera to acquire location", null);
        if (coordsEl) {
            coordsEl.textContent = "Coordinates will appear here after GPS lock.";
        }
        shutterBtn.disabled = true;
        retakeBtn.disabled = true;
        uploadBtn.disabled = true;
        setIdle(true);
        setMessage("", "");
    };

    const stopCamera = () => {
        if (stream) {
            stream.getTracks().forEach((track) => track.stop());
            stream = null;
        }
        if (watchId != null && navigator.geolocation) {
            navigator.geolocation.clearWatch(watchId);
            watchId = null;
        }
        if (video) {
            video.srcObject = null;
        }
        shutterBtn.disabled = true;
    };

    const startGps = () => {
        if (!navigator.geolocation) {
            gps.status = "denied";
            setGpsStatus("Location unavailable on this device", "is-err");
            return;
        }
        setGpsStatus("Acquiring location…", "is-warn");
        const onOk = (pos) => {
            const nextAcc = pos.coords.accuracy;
            // Keep the most accurate sample (lower meters = better).
            if (
                gps.lat != null &&
                gps.accuracy != null &&
                nextAcc != null &&
                nextAcc >= gps.accuracy
            ) {
                return;
            }
            gps = {
                lat: pos.coords.latitude,
                lng: pos.coords.longitude,
                accuracy: nextAcc,
                status: "ok",
            };
            const acc = Math.round(nextAcc || 0);
            const quality =
                acc <= 25 ? "is-ok" : acc <= 80 ? "is-warn" : "is-warn";
            setGpsStatus(
                acc > 80
                    ? `Weak GPS ±${acc} m — move outdoors / wait for better fix`
                    : `Location acquired: ±${acc} meters`,
                quality
            );
            if (coordsEl) {
                coordsEl.textContent =
                    `${gps.lat.toFixed(6)}, ${gps.lng.toFixed(6)} (±${acc} m)`;
            }
            if (blob) {
                uploadBtn.disabled = false;
            }
        };
        const onErr = (error) => {
            gps.status = "denied";
            if (error && error.code === 1) {
                setGpsStatus("Location access denied", "is-err");
                setMessage(
                    "Location access denied. Please enable location permission and try again.",
                    "alert-danger"
                );
            } else if (error && error.code === 3) {
                setGpsStatus("GPS timed out — wait in an open area", "is-warn");
                setMessage(
                    "GPS signal weak. Please wait a moment in an open area and try again, or enable high-accuracy mode.",
                    "alert-warning"
                );
            } else {
                setGpsStatus("Location unavailable", "is-err");
                setMessage(
                    "Location could not be determined. Enable GPS and try again.",
                    "alert-danger"
                );
            }
        };
        navigator.geolocation.getCurrentPosition(onOk, onErr, {
            enableHighAccuracy: true,
            timeout: 20000,
            maximumAge: 0,
        });
        watchId = navigator.geolocation.watchPosition(onOk, () => {}, {
            enableHighAccuracy: true,
            timeout: 20000,
            maximumAge: 0,
        });
    };

    const startCamera = async () => {
        if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
            httpsNote?.classList.remove("d-none");
            setMessage(
                "Camera and GPS need HTTPS (or localhost). Use a secure URL in production.",
                "alert-warning"
            );
            return;
        }
        if (!navigator.mediaDevices?.getUserMedia) {
            setMessage("Camera is not available in this browser.", "alert-danger");
            return;
        }
        if (isIOS) {
            iosNote?.classList.remove("d-none");
        }
        setMessage("", "");
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: { ideal: "environment" },
                    width: { ideal: 1920 },
                    height: { ideal: 1080 },
                },
                audio: false,
            });
            video.srcObject = stream;
            await video.play();
            shutterBtn.disabled = false;
            startGps();
            setIdle(false);
            livePanel.classList.remove("d-none");
            previewPanel.classList.add("d-none");
        } catch (error) {
            console.error(error);
            setMessage(
                "Camera access denied. Please enable camera permission in browser settings and try again.",
                "alert-danger"
            );
        }
    };

    const captureFrame = async () => {
        if (!stream) {
            return;
        }
        const width = video.videoWidth || 1280;
        const height = video.videoHeight || 720;
        const maxW = 1920;
        const maxH = 1080;
        let outW = width;
        let outH = height;
        if (outW > maxW || outH > maxH) {
            const scale = Math.min(maxW / outW, maxH / outH);
            outW = Math.round(outW * scale);
            outH = Math.round(outH * scale);
        }
        canvas.width = outW;
        canvas.height = outH;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, outW, outH);
        blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));
        if (!blob) {
            setMessage("Could not freeze the camera frame. Try again.", "alert-danger");
            return;
        }
        if (navigator.geolocation) {
            await new Promise((resolve) => {
                navigator.geolocation.getCurrentPosition(
                    (pos) => {
                        const nextAcc = pos.coords.accuracy;
                        if (
                            !(
                                gps.lat != null &&
                                gps.accuracy != null &&
                                nextAcc != null &&
                                nextAcc >= gps.accuracy
                            )
                        ) {
                            gps = {
                                lat: pos.coords.latitude,
                                lng: pos.coords.longitude,
                                accuracy: nextAcc,
                                status: "ok",
                            };
                        }
                        const acc = Math.round(gps.accuracy || 0);
                        setGpsStatus(
                            acc > 80
                                ? `Weak GPS ±${acc} m — prefer outdoors before upload`
                                : `Location acquired: ±${acc} meters`,
                            acc > 80 ? "is-warn" : "is-ok"
                        );
                        if (coordsEl) {
                            coordsEl.textContent =
                                `${gps.lat.toFixed(6)}, ${gps.lng.toFixed(6)} (±${acc} m)`;
                        }
                        resolve();
                    },
                    () => resolve(),
                    { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
                );
            });
        }
        showPreview(URL.createObjectURL(blob));
        stream.getTracks().forEach((track) => {
            if (track.kind === "video") {
                track.enabled = false;
            }
        });
    };

    const retake = async () => {
        if (preview.src) {
            URL.revokeObjectURL(preview.src);
        }
        blob = null;
        showLive();
        if (stream) {
            stream.getTracks().forEach((track) => {
                if (track.kind === "video") {
                    track.enabled = true;
                }
            });
            shutterBtn.disabled = false;
        } else {
            await startCamera();
        }
        setMessage("", "");
    };

    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    const uploadOnce = async () => {
        const form = new FormData();
        form.append("image", blob, `capture_${Date.now()}.jpg`);
        form.append("latitude", String(gps.lat));
        form.append("longitude", String(gps.lng));
        if (gps.accuracy != null) {
            form.append("accuracy", String(gps.accuracy));
        }
        form.append("timestamp", new Date().toISOString());
        const response = await fetch("/field_worker/api/capture", {
            method: "POST",
            body: form,
            credentials: "same-origin",
            headers: { "X-CSRFToken": csrfToken() },
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !body.success) {
            throw new Error(body.error || `Upload failed (${response.status})`);
        }
        return body;
    };

    const upload = async () => {
        if (uploading || !blob) {
            return;
        }
        if (gps.lat == null || gps.lng == null) {
            setMessage(
                "Location access denied. Please enable location permission and try again.",
                "alert-danger"
            );
            return;
        }
        uploading = true;
        uploadBtn.disabled = true;
        progressEl?.classList.remove("d-none");
        setMessage("Uploading inspection…", "alert-info");
        let lastError = null;
        for (let attempt = 0; attempt < 3; attempt += 1) {
            try {
                const result = await uploadOnce();
                progressEl?.classList.add("d-none");
                const detailHref = `/inspections/${result.inspection_id}`;
                setMessage("Upload successful. Opening inspection…", "alert-success");
                if (preview.src) {
                    URL.revokeObjectURL(preview.src);
                }
                stopCamera();
                uploading = false;
                window.location.href = detailHref;
                return;
            } catch (error) {
                lastError = error;
                await sleep(400 * (2 ** attempt));
            }
        }
        console.error(lastError);
        progressEl?.classList.add("d-none");
        setMessage(
            lastError?.message || "Upload failed. Please check your internet connection and try again.",
            "alert-danger"
        );
        uploading = false;
        uploadBtn.disabled = false;
    };

    startBtn?.addEventListener("click", startCamera);
    shutterBtn?.addEventListener("click", captureFrame);
    retakeBtn?.addEventListener("click", retake);
    uploadBtn?.addEventListener("click", upload);
    closeBtn?.addEventListener("click", () => {
        resetIdleUi();
    });
    window.addEventListener("beforeunload", stopCamera);

    setIdle(true);
    if (window.location.hash === "#live-capture") {
        root.scrollIntoView({ behavior: "smooth" });
    }
})();
