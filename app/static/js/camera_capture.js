(() => {
    const video = document.getElementById("camera-preview");
    const canvas = document.getElementById("camera-canvas");
    const statusEl = document.getElementById("camera-capture-status");
    const startBtn = document.getElementById("start-camera-button");
    const captureBtn = document.getElementById("capture-image-button");
    const stopBtn = document.getElementById("stop-camera-button");
    const openMapBtn = document.getElementById("open-map-after-capture");
    const fileInput = document.getElementById("media_file");

    if (!video || !canvas || !startBtn || !captureBtn) {
        return;
    }

    let stream = null;
    let busy = false;

    const setStatus = (message) => {
        if (statusEl) {
            statusEl.textContent = message;
        }
    };

    const csrfToken = () => {
        const input = document.querySelector('input[name="csrf_token"]');
        return input ? input.value : "";
    };

    const readGps = () =>
        new Promise((resolve) => {
            const latInput = document.getElementById("location_latitude");
            const lngInput = document.getElementById("location_longitude");
            const accInput = document.getElementById("gps_accuracy_meters");

            const existingLat = Number(latInput?.value);
            const existingLng = Number(lngInput?.value);
            if (Number.isFinite(existingLat) && Number.isFinite(existingLng)) {
                resolve({
                    lat: existingLat,
                    lng: existingLng,
                    accuracy: Number(accInput?.value) || null,
                    source: "form",
                });
                return;
            }

            if (!navigator.geolocation) {
                resolve(null);
                return;
            }

            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    resolve({
                        lat: pos.coords.latitude,
                        lng: pos.coords.longitude,
                        accuracy: pos.coords.accuracy,
                        source: "browser",
                    });
                },
                () => resolve(null),
                { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
            );
        });

    const applyGpsToForm = (gps) => {
        const latInput = document.getElementById("location_latitude");
        const lngInput = document.getElementById("location_longitude");
        const accInput = document.getElementById("gps_accuracy_meters");
        if (latInput) latInput.value = String(gps.lat);
        if (lngInput) lngInput.value = String(gps.lng);
        if (accInput && gps.accuracy != null) accInput.value = String(gps.accuracy);

        const alpineRoot = document.getElementById("inspection-form");
        if (alpineRoot && alpineRoot._x_dataStack && alpineRoot._x_dataStack[0]) {
            const data = alpineRoot._x_dataStack[0];
            data.latitude = String(gps.lat);
            data.longitude = String(gps.lng);
            data.accuracy = gps.accuracy != null ? String(gps.accuracy) : "";
            data.latDisplay = Number(gps.lat).toFixed(6);
            data.lngDisplay = Number(gps.lng).toFixed(6);
            data.accDisplay =
                gps.accuracy != null ? `± ${Math.round(gps.accuracy)} m` : "—";
            data.gpsTitle = "GPS captured";
            data.gpsMessage = `Source: ${gps.source}`;
            data.gpsCardClass = "gps-captured";
        }
    };

    const assignFileToInput = (file) => {
        if (!fileInput) {
            return;
        }
        const transfer = new DataTransfer();
        transfer.items.add(file);
        fileInput.files = transfer.files;
        fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    const pollTask = async (taskId, maxAttempts = 60) => {
        for (let i = 0; i < maxAttempts; i += 1) {
            const response = await fetch(`/api/tasks/${taskId}`, {
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            if (!response.ok) {
                throw new Error(`Task status HTTP ${response.status}`);
            }
            const body = await response.json();
            if (body.status === "SUCCEEDED" || body.status === "FAILED") {
                const result = await fetch(`/api/tasks/${taskId}/result`, {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                });
                return result.json();
            }
            setStatus(`Analyzing… ${Math.round(body.progress || 0)}%`);
            await sleep(400);
        }
        throw new Error("Analysis timed out");
    };

    const startCamera = async () => {
        if (!navigator.mediaDevices?.getUserMedia) {
            setStatus("Camera API unavailable in this browser");
            return;
        }
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: "environment" } },
                audio: false,
            });
            video.srcObject = stream;
            await video.play();
            captureBtn.disabled = false;
            stopBtn.disabled = false;
            setStatus("Camera live");
        } catch (error) {
            console.error(error);
            setStatus("Camera permission denied or unavailable");
        }
    };

    const stopCamera = () => {
        if (stream) {
            stream.getTracks().forEach((track) => track.stop());
            stream = null;
        }
        video.srcObject = null;
        captureBtn.disabled = true;
        stopBtn.disabled = true;
        setStatus("Camera stopped");
    };

    const captureAndAnalyze = async () => {
        if (busy) {
            return;
        }
        if (!stream) {
            setStatus("Start the camera first");
            return;
        }
        busy = true;
        captureBtn.disabled = true;
        setStatus("Capturing…");

        try {
            const width = video.videoWidth || 1280;
            const height = video.videoHeight || 720;
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext("2d");
            ctx.drawImage(video, 0, 0, width, height);

            const blob = await new Promise((resolve) =>
                canvas.toBlob(resolve, "image/jpeg", 0.92)
            );
            if (!blob) {
                throw new Error("Could not encode image");
            }

            const file = new File([blob], `capture_${Date.now()}.jpg`, {
                type: "image/jpeg",
            });
            assignFileToInput(file);

            setStatus("Reading GPS…");
            const gps = await readGps();
            if (!gps || gps.lat == null || gps.lng == null) {
                setStatus("GPS required — allow location or enter coordinates on the form, then capture again.");
                return;
            }
            applyGpsToForm(gps);

            const locationName =
                document.getElementById("location_name")?.value ||
                `Camera capture ${gps.lat.toFixed(5)}, ${gps.lng.toFixed(5)}`;
            if (document.getElementById("location_name")) {
                document.getElementById("location_name").value = locationName;
            }

            setStatus("Uploading…");
            const form = new FormData();
            form.append("file", file, file.name);
            form.append("location_name", locationName);
            form.append("location_latitude", String(gps.lat));
            form.append("location_longitude", String(gps.lng));
            form.append("gps_manual_override", "1");
            if (gps.accuracy != null) {
                form.append("gps_accuracy_meters", String(gps.accuracy));
            }
            form.append("notes", "Live camera capture");

            const uploadResponse = await fetch("/api/inspections/upload", {
                method: "POST",
                body: form,
                credentials: "same-origin",
                headers: {
                    "X-CSRFToken": csrfToken(),
                },
            });
            const uploadBody = await uploadResponse.json().catch(() => ({}));
            if (!uploadResponse.ok) {
                throw new Error(uploadBody.error || `Upload HTTP ${uploadResponse.status}`);
            }

            const taskId = uploadBody.analysis_task?.task_id;
            let severity = null;
            let taskResult = null;
            if (taskId) {
                setStatus("Analyzing…");
                taskResult = await pollTask(taskId);
                severity = taskResult?.result_summary?.severity?.level || null;
                if (taskResult?.status === "FAILED") {
                    throw new Error(taskResult.error_message || "Analysis failed");
                }
            }

            sessionStorage.setItem(
                "roadInspectionLatestCapture",
                JSON.stringify({
                    lat: gps.lat,
                    lng: gps.lng,
                    inspection_id: uploadBody.inspection_id,
                    severity,
                    timestamp: new Date().toISOString(),
                })
            );

            stopCamera();
            const inspectionId = uploadBody.inspection_id;
            if (inspectionId) {
                setStatus("Upload complete — opening inspection…");
                window.RoadInspectionUI?.toast?.(
                    "Capture analyzed — opening inspection",
                    "success"
                );
                window.location.href = "/inspections/" + inspectionId;
                return;
            }
            setStatus("Plotted! Open map to focus the new marker.");
            if (openMapBtn) {
                openMapBtn.classList.remove("d-none");
            }
            window.RoadInspectionUI?.toast?.("Capture analyzed — open map to view", "success");
        } catch (error) {
            console.error(error);
            setStatus(error.message || "Capture failed");
        } finally {
            busy = false;
            captureBtn.disabled = !stream;
        }
    };

    startBtn.addEventListener("click", startCamera);
    stopBtn?.addEventListener("click", stopCamera);
    captureBtn.addEventListener("click", captureAndAnalyze);

    window.addEventListener("beforeunload", stopCamera);
})();
