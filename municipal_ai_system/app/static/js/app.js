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