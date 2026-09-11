"""
Legacy / alternate auth blueprint (Shreya earlier draft).

NOT registered by create_app(). Phase-3 login lives in app.auth
(LoginForm + UserIdentity + authenticate). Keep this file for reference
only — registering it would conflict with Blueprint name "auth".
"""

from urllib.parse import urljoin, urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.extensions import db
from app.models.user import User


auth_bp = Blueprint("legacy_auth_unused", __name__, url_prefix="/legacy-auth")


def is_safe_redirect_url(target: str) -> bool:
    if not target:
        return False

    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))

    return (
        redirect_url.scheme in ("http", "https")
        and host_url.netloc == redirect_url.netloc
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username_or_email = (
            request.form.get("username_or_email", "")
            .strip()
            .lower()
        )
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"

        if not username_or_email or not password:
            flash("Enter both username/email and password.", "danger")
            return render_template("auth/login.html")

        user = (
            User.query.filter(
                db.or_(
                    db.func.lower(User.username) == username_or_email,
                    db.func.lower(User.email) == username_or_email
                )
            )
            .first()
        )

        if user is None or not user.check_password(password):
            if user is not None:
                user.register_failed_login()
                db.session.commit()

            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html")

        if not user.is_active:
            flash(
                "This account is inactive. Please contact the administrator.",
                "danger"
            )
            return render_template("auth/login.html")

        if not user.is_verified:
            flash(
                "This account is not verified. Please contact the administrator.",
                "warning"
            )
            return render_template("auth/login.html")

        user.mark_login_success()
        db.session.commit()

        login_user(user, remember=remember)

        flash(
            f"Welcome, {user.full_name}. You are logged in as {user.role_name}.",
            "success"
        )

        next_url = request.args.get("next")

        if next_url and is_safe_redirect_url(next_url):
            return redirect(next_url)

        return redirect(url_for("dashboard.index"))

    return render_template("auth/login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    if current_user.is_authenticated:
        logout_user()
        flash("You have been logged out successfully.", "success")

    return redirect(url_for("auth.login"))