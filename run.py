import os
import signal
import sys
from app import create_app
from app.extensions import socketio
from app.services.background_sender import BackgroundSender

app = create_app(os.getenv('APP_ENV', 'development'))

# Start the background worker automatically.
# This works for both `python run.py` and `gunicorn run:app` (1 worker) 
# without needing any separate commands or Redis!
if not hasattr(app, 'bg_sender'):
    bg_sender = BackgroundSender()
    bg_sender.start(app)
    app.bg_sender = bg_sender

if __name__ == '__main__':
    def _shutdown(signum, frame):
        print(f"\nReceived signal {signum}, shutting down...")
        if app.bg_sender:
            app.bg_sender.stop()
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
