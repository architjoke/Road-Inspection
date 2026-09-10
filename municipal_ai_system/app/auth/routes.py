from urllib.parse import urlparse

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import auth_bp
from app.auth.audit import write_super_admin_audit, write_user_audit
from app.auth.forms import LoginForm
from app.auth.services import authenticate
from app.extensions import limiter


def _safe_next(value):
    if not value:
        return url_for('dashboard.index')

    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or '\\' in value:
        return url_for('dashboard.index')
    if value.startswith('/') and not value.startswith('//'):
        return value
    return url_for('dashboard.index')


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    form = LoginForm()
    if form.validate_on_submit():
        identity, error = authenticate(form.identifier.data, form.password.data)
        if identity is None:
            flash(error, 'danger')
            return render_template('auth/login.html', form=form)

        login_user(identity, remember=form.remember.data)
        flash(f'Signed in as {identity.full_name}.', 'success')
        return redirect(_safe_next(request.args.get('next')))

    return render_template('auth/login.html', form=form)


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    identity = current_user
    if getattr(identity, 'is_super_admin', False):
        write_super_admin_audit(
            action_type='LOGOUT',
            action_description='Super admin signed out.',
            super_admin_id=identity.pk,
            entity_id=identity.pk,
            response_status=200,
        )
    else:
        write_user_audit(
            action_type='LOGOUT',
            action_description='User signed out.',
            user_id=identity.pk,
            entity_id=identity.pk,
            response_status=200,
        )

    logout_user()
    flash('You have been signed out.', 'info')
    return redirect(url_for('auth.login'))
