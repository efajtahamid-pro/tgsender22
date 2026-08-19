import os
import signal
import sys
import fcntl
from app import create_app
from app.extensions import socketio
from app.services.background_sender import BackgroundSender

app = create_app(os.getenv('APP_ENV', 'development'))

# FIX: Use a file lock to ensure only ONE Gunicorn worker starts the background sender.
# This allows you to run `gunicorn run:app` with multiple workers safely.
bg_sender = None
lock_file = open('/tmp/tg_platform.lock', 'w')
try:
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    bg_sender = BackgroundSender()
    bg_sender.start(app)
    app.bg_sender = bg_sender
except IOError:
    # Another worker process already started the background sender
    pass

if __name__ == '__main__':
    def _shutdown(signum, frame):
        print(f"\nReceived signal {signum}, shutting down...")
        if bg_sender:
            bg_sender.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("🌐 Starting Web Server on http://0.0.0.0:5000")
    print("📨 Background worker started automatically in-process")
    socketio.run(
        app,
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=app.config.get('DEBUG', False),
        allow_unsafe_werkzeug=True
    )
