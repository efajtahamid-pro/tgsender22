import logging
from flask import render_template, jsonify, request
from werkzeug.exceptions import HTTPException
from app.extensions import db

logger = logging.getLogger(__name__)

def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Not found', 'status': 404}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(error):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Forbidden', 'status': 403}), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(500)
    def internal_error(error):
        logger.exception('Internal server error', extra={'error_type': type(error).__name__})
        # A DB error (e.g. an IntegrityError) leaves the session in a broken
        # "pending rollback" state. If we don't roll it back here, the very
        # next query in this request -- e.g. base.html checking
        # current_user.is_authenticated -- raises again and the error page
        # itself fails to render.
        db.session.rollback()
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error', 'status': 500}), 500
        return render_template('errors/500.html'), 500

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        # HTTPException covers things like CSRF failures (400), method-not-
        # allowed (405), etc. Without this check every one of those was
        # being reported to the user as a generic 500, which is both
        # misleading and hides the real status code from clients/logs.
        if isinstance(error, HTTPException):
            db.session.rollback()
            logger.warning('HTTP exception', extra={'error_type': type(error).__name__, 'status': error.code})
            if request.path.startswith('/api/'):
                return jsonify({'error': error.description, 'status': error.code}), error.code
            return render_template('errors/500.html', error=error), error.code

        logger.exception('Unexpected exception', extra={'error_type': type(error).__name__})
        db.session.rollback()
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error', 'status': 500}), 500
        return render_template('errors/500.html'), 500
