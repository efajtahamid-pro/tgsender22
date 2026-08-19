import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-change-me')
    APP_ENV = os.getenv('APP_ENV', 'development')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

    # FIX: was 'sqlite:///instance/platform.db' which resolved to
    #      instance/instance/platform.db (nested dir never created)
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///platform.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}

    API_ID = int(os.getenv('API_ID', 0))
    API_HASH = os.getenv('API_HASH', '')

    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@example.com')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'Admin@123456')

    TOTP_ISSUER = os.getenv('TOTP_ISSUER', 'TelegramBotPlatform')
    LOGIN_MAX_ATTEMPTS = int(os.getenv('LOGIN_MAX_ATTEMPTS', 5))
    LOGIN_LOCKOUT_MINUTES = int(os.getenv('LOGIN_LOCKOUT_MINUTES', 15))
    SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    PERMANENT_SESSION_LIFETIME = int(os.getenv('SESSION_LIFETIME', 86400))
    WTF_CSRF_TIME_LIMIT = 3600

    # Session string encryption at rest (Fernet)
    SESSION_ENCRYPTION_KEY = os.getenv('SESSION_ENCRYPTION_KEY', '')

    # SocketIO cross-process message queue (Redis)
    SOCKETIO_MESSAGE_QUEUE = os.getenv('SOCKETIO_MESSAGE_QUEUE', '')
    SOCKETIO_ASYNC_MODE = os.getenv('SOCKETIO_ASYNC_MODE', 'threading')

    DEFAULT_DAILY_LIMIT = int(os.getenv('DEFAULT_DAILY_LIMIT', 50))
    RATE_LIMIT_DEFAULT = os.getenv('RATE_LIMIT_DEFAULT', '200 per minute')

    MAX_RETRY_ATTEMPTS = int(os.getenv('MAX_RETRY_ATTEMPTS', 3))
    RETRY_BASE_DELAY = float(os.getenv('RETRY_BASE_DELAY', 2.0))
    CAMPAIGN_SEND_DELAY = float(os.getenv('CAMPAIGN_SEND_DELAY', '2'))
    CAMPAIGN_BATCH_SIZE = int(os.getenv('CAMPAIGN_BATCH_SIZE', 50))

    REPLY_POLL_INTERVAL_SECONDS = int(os.getenv('REPLY_POLL_INTERVAL_SECONDS', 30))

    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    LOG_FILE = os.getenv('LOG_FILE', 'logs/app.log')


class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False

config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}
