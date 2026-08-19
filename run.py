import os
import signal
import sys
from app import create_app
from app.extensions import socketio
from app.services.background_sender import BackgroundSender

app = create_app(os.getenv('APP_ENV', 'development'))
bg_sender = None

if __name__ == '__main__':
    # ─── Dev mode: start both web + worker in one process ───
    bg_sender = BackgroundSender()
    bg_sender.start(app)
    app.bg_sender = bg_sender

    def _shutdown(signum, frame):
        print(f"\nReceived signal {signum}, shutting down...")
        if bg_sender:
            bg_sender.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("🌐 Starting Web Server on http://0.0.0.0:5000")
    print("📨 Background worker started in-process (dev mode)")
    socketio.run(
        app,
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=app.config.get('DEBUG', False),
        allow_unsafe_werkzeug=True
    )
