document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("inspection-form");
    const locationButton = document.getElementById("capture-location-button");

    const fileInput = document.getElementById("media_file");
    const dropZone = document.getElementById("upload-drop-zone");

    const fileInfo = document.getElementById("selected-file-info");
    const fileName = document.getElementById("selected-file-name");
    const fileSize = document.getElementById("selected-file-size");

    const latitudeInput = document.getElementById("location_latitude");
    const longitudeInput = document.getElementById("location_longitude");

    if (locationButton) {
        locationButton.addEventListener("click", () => {
            window.RoadInspectionGPS.captureLocation();
        });
    }

    const showSelectedFile = (file) => {
        if (!file) {
            fileInfo.classList.add("d-none");
            return;
        }

        const sizeInMb = file.size / (1024 * 1024);

        fileName.textContent = file.name;
        fileSize.textContent = `(${sizeInMb.toFixed(2)} MB)`;

        fileInfo.classList.remove("d-none");
    };

    if (fileInput) {
        fileInput.addEventListener("change", () => {
            showSelectedFile(fileInput.files[0]);
        });
    }

    if (dropZone && fileInput) {
        ["dragenter", "dragover"].forEach((eventName) => {
            dropZone.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropZone.classList.add("drag-over");
            });
        });

        ["dragleave", "drop"].forEach((eventName) => {
            dropZone.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropZone.classList.remove("drag-over");
            });
        });

        dropZone.addEventListener("drop", (event) => {
            const droppedFiles = event.dataTransfer.files;

            if (droppedFiles.length > 0) {
                fileInput.files = droppedFiles;
                showSelectedFile(droppedFiles[0]);
            }
        });
    }

    if (form) {
        form.addEventListener("submit", (event) => {
            if (!fileInput.files.length) {
                event.preventDefault();
                alert("Please choose a road image or video file.");
                return;
            }

            if (!latitudeInput.value || !longitudeInput.value) {
                event.preventDefault();

                alert(
                    "Please capture your current GPS location before submitting."
                );
            }
        });
    }
});