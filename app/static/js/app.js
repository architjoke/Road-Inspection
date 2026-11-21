window.RoadInspectionUI = {
    toast(message, variant = "info") {
        const stack = document.getElementById("app-toast-stack");
        if (!stack) {
            window.alert(message);
            return;
        }

        const tone = {
            success: "text-bg-success",
            danger: "text-bg-danger",
            warning: "text-bg-warning",
            info: "text-bg-primary",
        }[variant] || "text-bg-primary";

        const el = document.createElement("div");
        el.className = `toast align-items-center ${tone} border-0 show`;
        el.setAttribute("role", "alert");
        el.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        stack.appendChild(el);

        setTimeout(() => {
            el.classList.remove("show");
            el.remove();
        }, 4200);
    },

    confirmAction(message) {
        return window.confirm(message);
    },
};

document.addEventListener("DOMContentLoaded", () => {
    const togglePasswordButton = document.getElementById("toggle-password");
    const passwordInput = document.getElementById("password");

    if (togglePasswordButton && passwordInput) {
        togglePasswordButton.addEventListener("click", () => {
            const shouldShowPassword = passwordInput.type === "password";

            passwordInput.type = shouldShowPassword ? "text" : "password";

            togglePasswordButton.innerHTML = shouldShowPassword
                ? '<i class="fa-solid fa-eye-slash"></i>'
                : '<i class="fa-solid fa-eye"></i>';
        });
    }
});
