import pyotp
import qrcode
import io
import base64
import re
from datetime import datetime, timedelta
from flask import current_app
from app.extensions import db
from app.models import User

def validate_strong_password(password: str):
    if not password or len(password) < 10:
        return False, 'Password must be at least 10 characters long.'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain an uppercase letter.'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain a lowercase letter.'
    if not re.search(r'\d', password):
        return False, 'Password must contain a digit.'
    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\/~`]', password):
        return False, 'Password must contain a special character.'
    return True, None

def generate_totp_secret() -> str:
    return pyotp.random_base32()

def get_totp_uri(user: User) -> str:
    issuer = current_app.config.get('TOTP_ISSUER', 'TelegramBotPlatform')
    return pyotp.totp.TOTP(user.totp_secret).provisioning_uri(
        name=user.username, issuer_name=issuer
    )

def generate_qr_base64(data_uri: str) -> str:
    img = qrcode.make(data_uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def verify_totp(user: User, token: str) -> bool:
    if not user.totp_secret:
        return False
    totp = pyotp.TOTP(user.totp_secret)
    return totp.verify(token, valid_window=1)

def register_failed_login(user: User):
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    max_attempts = current_app.config.get('LOGIN_MAX_ATTEMPTS', 5)
    if user.failed_login_attempts >= max_attempts:
        user.locked_until = datetime.utcnow() + timedelta(
            minutes=current_app.config.get('LOGIN_LOCKOUT_MINUTES', 15)
        )
    db.session.commit()

def reset_failed_login(user: User):
    user.failed_login_attempts = 0
    user.locked_until = None
    db.session.commit()

def is_locked(user: User) -> bool:
    if not user.locked_until:
        return False
    if datetime.utcnow() < user.locked_until:
        return True
    user.locked_until = None
    user.failed_login_attempts = 0
    db.session.commit()
    return False