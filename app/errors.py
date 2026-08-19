import logging
from flask import render_template, jsonify, request

logger = logging.getLogger(__name__)

def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith('/api/'): return jsonify({'error': 'Not found', 'status': 404}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(error):
        if request.path.startswith('/api/'): return jsonify({'error': 'Forbidden', 'status': 403}), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(500)
    def internal_error(error):
        logger.exception('Internal server error', extra={'error_type': type(error).__name__})
        if request.path.startswith('/api/'): return jsonify({'error': 'Internal server error', 'status': 500}), 500
        return render_template('errors/500.html'), 500

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        logger.exception('Unexpected exception', extra={'error_type': type(error).__name__})
        if request.path.startswith('/api/'): return jsonify({'error': 'Internal server error', 'status': 500}), 500
        return render_template('errors/500.html'), 500
