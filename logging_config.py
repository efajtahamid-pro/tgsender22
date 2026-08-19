import os
import logging
from pythonjsonlogger import jsonlogger
from flask import has_request_context, request

class ContextFilter(logging.Filter):
    def filter(self, record):
        record.service = 'telegram-bot-platform'
        record.environment = os.getenv('APP_ENV', 'development')
        if has_request_context():
            record.request_path = request.path
            record.request_method = request.method
            record.remote_ip = request.remote_addr
            try:
                from flask_login import current_user
                record.user_id = current_user.id if current_user.is_authenticated else None
                record.user_role = current_user.role if current_user.is_authenticated else None
            except Exception:
                record.user_id = None
                record.user_role = None
        else:
            record.request_path = None
            record.request_method = None
            record.remote_ip = None
            record.user_id = None
            record.user_role = None

        for f in ('campaign_id', 'account_id', 'account_phone', 'recipient_id', 'status', 'conversation_id', 'error_type', 'task_id'):
            if not hasattr(record, f):
                setattr(record, f, None)
        return True

def configure_logging(app):
    log_level = app.config.get('LOG_LEVEL', 'INFO')
    log_file = app.config.get('LOG_FILE', 'logs/app.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s %(service)s %(environment)s %(user_id)s %(user_role)s %(request_method)s %(request_path)s %(remote_ip)s %(campaign_id)s %(account_id)s %(account_phone)s %(recipient_id)s %(status)s %(conversation_id)s %(error_type)s %(task_id)s'
    )

    context_filter = ContextFilter()
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)
    file_handler.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    console_handler.setLevel(log_level)

    root = logging.getLogger()
    root.handlers = [console_handler, file_handler]
    root.setLevel(log_level)

    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)
    logging.getLogger('telethon').setLevel(logging.WARNING)
