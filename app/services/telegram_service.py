import os
import asyncio
import threading
import time
import socket
import socks
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError
from app.extensions import db
from app.models import TelegramAccount, Proxy
from datetime import datetime

logger = logging.getLogger(__name__)

class TelegramService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized: return
        self.api_id = int(os.getenv('API_ID', 0))
        self.api_hash = os.getenv('API_HASH', '')
        self.clients = {}
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, daemon=True).start()
        self._initialized = True

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=120)

    def test_proxy_connection(self, proxy: Proxy):
        """Tests proxy reachability without Telegram."""
        try:
            s = socks.socksocket()
            if proxy.proxy_type == 'socks5': s.set_proxy(socks.SOCKS5, proxy.host, proxy.port, True, proxy.username, proxy.password)
            elif proxy.proxy_type == 'http': s.set_proxy(socks.HTTP, proxy.host, proxy.port, True, proxy.username, proxy.password)
            
            s.settimeout(10)
            start = time.time()
            s.connect(("api.telegram.org", 443))
            latency = int((time.time() - start) * 1000)
            s.close()
            return True, latency, None
        except Exception as e:
            return False, 0, str(e)

    def test_account_health(self, account: TelegramAccount):
        """Verifies Telegram session and proxy health."""
        async def _check():
            client = await self._ensure_connected(account)
            if not client:
                return False, "Client connection failed"
            try:
                me = await client.get_me()
                if me:
                    return True, "Healthy"
                return False, "Unauthorized"
            except Exception as e:
                return False, str(e)

        try:
            # Check proxy first
            if account.proxy:
                p_ok, _, p_err = self.test_proxy_connection(account.proxy)
                if not p_ok:
                    return False, f"Proxy failed: {p_err}"

            # Check Telegram
            ok, msg = self._run_async(_check())
            return ok, msg
        except Exception as e:
            return False, str(e)

    # ... (Keep existing methods: _get_proxy, _build_client, _ensure_connected, init_all_clients, send_code, verify_code, send_message_sync, fetch_dialogs_sync, fetch_dialog_messages_sync)
