from flask import (Blueprint, render_template, redirect, url_for, flash, request, session, jsonify)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime
from app.extensions import db, limiter
from app.models import User
from app.services.auth_service import (
    validate_strong_password, register_failed_login, reset_failed_login,
    is_locked, verify_totp, generate_totp_secret, get_totp_uri, generate_qr_base64
)

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard' if current_user.role == 'admin' else 'employee.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        totp_code = request.form.get('totp_code', '').strip()

        user = db.session.query(User).filter_by(username=username).first()

        if not user or not check_password_hash(user.password_hash, password):
            if user:
                register_failed_login(user)
            flash('Invalid credentials', 'danger')
            return render_template('auth/login.html')

        if not user.is_active:
            flash('Account disabled.', 'danger')
            return render_template('auth/login.html')

        if is_locked(user):
            mins = int((user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
            flash(f'Account locked. Try again in {mins} min.', 'danger')
            return render_template('auth/login.html')

        if user.role == 'admin' and user.is_2fa_enabled:
            if not totp_code:
                flash('2FA code required.', 'info')
                return render_template('auth/login.html', require_2fa=True, username=username)
            if not verify_totp(user, totp_code):
                register_failed_login(user)
                flash('Invalid 2FA code.', 'danger')
                return render_template('auth/login.html', require_2fa=True, username=username)

        login_user(user)
        reset_failed_login(user)
        user.last_login = datetime.utcnow()
        db.session.commit()

        return redirect(url_for('admin.dashboard' if user.role == 'admin' else 'employee.dashboard'))

    return render_template('auth/login.html')

@auth_bp.route('/2fa/setup', methods=['GET', 'POST'])
@login_required
def setup_2fa():
    if request.method == 'POST':
        action = request.form.get('action')
        code = request.form.get('code', '').strip()

        if action == 'enable':
            secret = session.get('pending_totp_secret')
            if not secret:
                flash('Session expired. Try again.', 'warning')
                return redirect(url_for('auth.setup_2fa'))

            current_user.totp_secret = secret
            current_user.is_2fa_enabled = True
            db.session.commit()
            session.pop('pending_totp_secret', None)
            flash('2FA enabled successfully.', 'success')
            return redirect(url_for('auth.setup_2fa'))

        if action == 'verify':
            if verify_totp(current_user, code):
                flash('2FA code valid.', 'success')
            else:
                flash('Invalid 2FA code.', 'danger')
            return redirect(url_for('auth.setup_2fa'))

        if action == 'disable':
            current_user.totp_secret = None
            current_user.is_2fa_enabled = False
            db.session.commit()
            flash('2FA disabled.', 'info')
            return redirect(url_for('auth.setup_2fa'))

    if not current_user.is_2fa_enabled and 'pending_totp_secret' not in session:
        session['pending_totp_secret'] = generate_totp_secret()

    qr_b64 = None
    uri = None
    if not current_user.is_2fa_enabled:
        class _TmpUser: pass
        tmp = _TmpUser()
        tmp.totp_secret = session.get('pending_totp_secret')
        tmp.username = current_user.username
        uri = get_totp_uri(tmp)
        qr_b64 = generate_qr_base64(uri)

    return render_template('auth/setup_2fa.html', qr_b64=qr_b64, secret=session.get('pending_totp_secret'), totp_uri=uri, is_enabled=current_user.is_2fa_enabled)

@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_pw = request.form.get('old_password')
        new_pw = request.form.get('new_password')

        if not check_password_hash(current_user.password_hash, old_pw):
            flash('Current password incorrect.', 'danger')
            return redirect(url_for('auth.change_password'))

        ok, err = validate_strong_password(new_pw)
        if not ok:
            flash(err, 'danger')
            return redirect(url_for('auth.change_password'))

        current_user.password_hash = generate_password_hash(new_pw)
        db.session.commit()
        flash('Password changed.', 'success')
        return redirect(url_for('admin.dashboard' if current_user.role == 'admin' else 'employee.dashboard'))

    return render_template('auth/change_password.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))