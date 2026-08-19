import os
from flask import Flask, jsonify
from werkzeug.security import generate_password_hash
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from app.extensions import (
    db, login_manager, migrate, csrf, cors, bcrypt, limiter, socketio
)
from app.config import config_map
from app.logging_config import configure_logging
from app.errors import register_error_handlers

def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('APP_ENV', 'development')

    app = Flask(__name__)
    app.config.from_object(config_map[config_name])

    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if db_uri.startswith('postgres://'):
        db_uri = db_uri.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = db_uri

    if 'sqlite' in db_uri:
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'poolclass': NullPool,
            'connect_args': {'check_same_thread': False}
        }
    else:
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    cors.init_app(app)
    bcrypt.init_app(app)
    limiter.init_app(app)
    socketio.init_app(app, async_mode=app.config.get('SOCKETIO_ASYNC_MODE', 'threading'), cors_allowed_origins='*')

    configure_logging(app)
    register_error_handlers(app)

    if 'sqlite' in db_uri:
        with app.app_context():
            @event.listens_for(db.engine, 'connect')
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA synchronous=NORMAL')
                cursor.close()

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return db.session.get(User, int(user_id))

    with app.app_context():
        _create_tables_and_admin(app)

    from app.routes.auth import auth_bp
    from app.routes.admin import admin_bp
    from app.routes.employee import employee_bp
    from app.routes.api import api_bp
    from app.routes.sockets import register_socket_handlers

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(employee_bp, url_prefix='/employee')
    app.register_blueprint(api_bp, url_prefix='/api')
    register_socket_handlers(socketio)

    @app.route('/health')
    def health_check():
        try:
            db.session.execute(db.text('SELECT 1'))
            return jsonify({'status': 'healthy', 'database': 'connected'}), 200
        except Exception as e:
            return jsonify({'status': 'unhealthy', 'error': str(e)}), 503

    return app

def _create_tables_and_admin(app):
    from app.models import User
    db.create_all()
    if not db.session.query(User).filter_by(username=app.config['ADMIN_USERNAME']).first():
        admin = User(
            username=app.config['ADMIN_USERNAME'],
            email=app.config['ADMIN_EMAIL'],
            password_hash=generate_password_hash(app.config['ADMIN_PASSWORD']),
            role='admin', is_active=True,
            can_add_proxies=True, can_add_numbers=True, can_handle_replies=True,
        )
        db.session.add(admin)
        db.session.commit()
