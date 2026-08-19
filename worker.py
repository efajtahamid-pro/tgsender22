#!/usr/bin/env python
"""
Standalone background worker for campaign sending and reply polling.

Run separately in production:
    python worker.py

This process handles:
  - Campaign message sending (pending → sending → sent/dead_letter)
  - Reply polling (fetches incoming messages from Telegram)
  - Daily limit resets
  - Worker heartbeat updates

The web (gunicorn) process handles HTTP requests only.
SocketIO events emitted here reach web clients via Redis message queue
(set SOCKETIO_MESSAGE_QUEUE in .env).
"""
import os
import sys
import signal
import time
import logging

from app import create_app
from app.extensions import socketio
from app.services.background_sender import BackgroundSender

logger = logging.getLogger(__name__)


def main():
    env = os.getenv('APP_ENV', 'production')
    app = create_app(env)

    sender = BackgroundSender()
    sender.start(app)

    def handle_shutdown(signum, frame):
        logger.info("Received signal %s, shutting down worker...", signum)
        sender.stop()
        # Give threads a moment to clean up
        time.sleep(2)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    logger.info("=" * 60)
    logger.info("  Telegram Bot Platform — Background Worker")
    logger.info("  Environment: %s", env)
    logger.info("  PID: %d", os.getpid())
    logger.info("=" * 60)
    logger.info("Worker running. Press Ctrl+C to stop.")

    # Keep main thread alive
    while sender.running:
        time.sleep(1)

    logger.info("Worker stopped.")


if __name__ == '__main__':
    main()
