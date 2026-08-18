import os
import threading
from app import create_app
from app.extensions import socketio

app = create_app(os.getenv('APP_ENV', 'development'))

def start_worker(target, name):
    """Helper to start a worker as a daemon thread."""
    thread = threading.Thread(target=target, daemon=True, name=name)
    thread.start()
    print(f"✅ {name} started in background.")

if __name__ == '__main__':
    # 1. Start the Campaign Worker
    from campaign_worker import run as run_campaign_worker
    start_worker(run_campaign_worker, "CampaignWorker")
    
    # 2. Start the Reply Worker
    from reply_worker import run as run_reply_worker
    start_worker(run_reply_worker, "ReplyWorker")

    # 3. Start the Flask Web Server & Socket.IO
    print("🌐 Starting Web Server on http://0.0.0.0:5000")
    socketio.run(
        app, 
        host='0.0.0.0', 
        port=int(os.getenv('PORT', 5000)), 
        debug=app.config.get('DEBUG', False),
        allow_unsafe_werkzeug=True  # Required for Werkzeug 3.x in production
    )
