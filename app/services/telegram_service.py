import os
import asyncio
import threading
import time
import socks
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, PhoneCodeInvalidError, PhoneCodeExpiredError,
    SessionPasswordNeededError, RPCError, UsernameNotOccupiedError,
    UsernameInvalidError, PeerIdInvalidError,
)
from app.extensions import db
from app.models import TelegramAccount, Proxy
from app.services.session_crypto import encrypt_session, decrypt_session
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
        if self._initialized:
            return
        
        # FIX: Safe parsing of API_ID to prevent background worker crashes
        try:
            self.api_id = int(os.getenv('API_ID', 0))
        except (ValueError, TypeError):
            self.api_id = 0
            logger.error("Invalid API_ID in environment variables. Must be an integer.")
            
        self.api_hash = os.getenv('API_HASH', '')
        self.clients = {}
        self._client_locks = {}
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, daemon=True).start()
        self._initialized = True

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro, timeout=30):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def _get_proxy(self, proxy):
        if not proxy:
            return None
        return (proxy.proxy_type, proxy.host, proxy.port, True, proxy.username, proxy.password)

    def _get_client_lock(self, account_id):
        if account_id not in self._client_locks:
            self._client_locks[account_id] = threading.Lock()
        return self._client_locks[account_id]

    async def _build_client(self, account):
        proxy = self._get_proxy(account.proxy)
        session_string = decrypt_session(account.session_string) if account.session_string else ''
        session = StringSession(session_string)
        client = TelegramClient(
            session, self.api_id, self.api_hash,
            proxy=proxy, timeout=10, connection_retries=2, retry_delay=1
        )
        return client

    async def _ensure_connected(self, account):
        with self._get_client_lock(account.id):
            if account.id in self.clients:
                client = self.clients[account.id]
                if client.is_connected():
                    if await client.is_user_authorized():
                        return client
                    else:
                        await client.disconnect()
                        del self.clients[account.id]
                else:
                    del self.clients[account.id]

            client = await self._build_client(account)
            await client.connect()
            if not await client.is_user_authorized():
                if not account.session_string:
                    return None
                await client.disconnect()
                return None
            self.clients[account.id] = client
            return client

    def init_all_clients(self):
        with db.session.no_autoflush:
            accounts = db.session.query(TelegramAccount).filter_by(is_active=True, is_verified=True).all()
        for acc in accounts:
            try:
                client = self.get_client(acc)
                if client:
                    acc.health_status = 'healthy'
                    acc.last_successful_connection = datetime.utcnow()
                    acc.last_error = None
                else:
                    acc.health_status = 'disconnected'
                    acc.last_error = 'Failed to authorize'
            except TimeoutError:
                acc.health_status = 'disconnected'
                acc.last_error = 'Connection timed out (Check Proxy)'
            except Exception as e:
                acc.health_status = 'disconnected'
                acc.last_error = str(e)
            db.session.commit()

    def get_client(self, account):
        return self._run_async(self._ensure_connected(account), timeout=20)

    def test_proxy_connection(self, proxy: Proxy):
        try:
            s = socks.socksocket()
            if proxy.proxy_type == 'socks5':
                s.set_proxy(socks.SOCKS5, proxy.host, proxy.port, True, proxy.username, proxy.password)
            elif proxy.proxy_type == 'http':
                s.set_proxy(socks.HTTP, proxy.host, proxy.port, True, proxy.username, proxy.password)
            s.settimeout(10)
            start = time.time()
            s.connect(("api.telegram.org", 443))
            latency = int((time.time() - start) * 1000)
            s.close()
            return True, latency, None
        except Exception as e:
            return False, 0, str(e)

    def test_account_health(self, account: TelegramAccount):
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
            if account.proxy:
                p_ok, _, p_err = self.test_proxy_connection(account.proxy)
                if not p_ok:
                    return False, f"Proxy failed: {p_err}"
            ok, msg = self._run_async(_check(), timeout=20)
            return ok, msg
        except Exception as e:
            return False, str(e)

    def send_code(self, account):
        async def _send():
            proxy = self._get_proxy(account.proxy)
            client = TelegramClient(
                StringSession(), self.api_id, self.api_hash,
                proxy=proxy, timeout=10, connection_retries=2
            )
            await client.connect()
            result = await client.send_code_request(account.phone)
            account.session_string = encrypt_session(client.session.save())
            db.session.commit()
            self.clients[account.id] = client
            return result.phone_code_hash

        try:
            hash_val = self._run_async(_send(), timeout=20)
            return {'status': 'success', 'phone_code_hash': hash_val}
        except FloodWaitError as e:
            return {'status': 'error', 'message': f'Flood wait: {e.seconds}s'}
        except Exception as e:
            logger.exception('send_code failed', extra={'account_phone': account.phone})
            return {'status': 'error', 'message': str(e)}

    def verify_code(self, account, code, phone_code_hash, password=None):
        async def _sign_in():
            client = self.clients.get(account.id)
            if not client:
                client = await self._build_client(account)
                await client.connect()
                self.clients[account.id] = client
            try:
                await client.sign_in(phone=account.phone, code=code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                if password:
                    await client.sign_in(password=password)
                else:
                    return {'status': 'error', 'message': '2FA password required', 'requires_password': True}

            if await client.is_user_authorized():
                account.session_string = encrypt_session(client.session.save())
                account.is_verified = True
                account.health_status = 'healthy'
                account.last_successful_connection = datetime.utcnow()
                db.session.commit()
                return {'status': 'success'}
            else:
                return {'status': 'error', 'message': 'Not authorized'}

        try:
            return self._run_async(_sign_in(), timeout=20)
        except PhoneCodeInvalidError:
            return {'status': 'error', 'message': 'Invalid code'}
        except PhoneCodeExpiredError:
            return {'status': 'error', 'message': 'Code expired'}
        except SessionPasswordNeededError:
            return {'status': 'error', 'message': '2FA password required', 'requires_password': True}
        except Exception as e:
            logger.exception('verify_code failed', extra={'account_phone': account.phone})
            return {'status': 'error', 'message': str(e)}

    def _classify_error(self, exc):
        if isinstance(exc, FloodWaitError):
            return True, 'FloodWaitError'
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return True, 'ConnectionError'
        if isinstance(exc, (UsernameNotOccupiedError, UsernameInvalidError)):
            return False, 'UsernameInvalid'
        if isinstance(exc, PeerIdInvalidError):
            return False, 'PeerIdInvalid'
        if isinstance(exc, RPCError):
            return True, type(exc).__name__
        return False, type(exc).__name__

    def send_message_sync(self, account, target, message, retries=3, base_delay=2.0,
                          campaign_id=None, recipient_db_id=None):
        extra_base = {'account_phone': account.phone, 'campaign_id': campaign_id, 'recipient_id': recipient_db_id}

        for attempt in range(1, retries + 1):
            try:
                client = self.get_client(account)
                if not client:
                    if attempt == retries:
                        return {'status': 'error', 'message': 'Client not available',
                                'permanent': False, 'attempt': attempt}
                    time.sleep(base_delay * attempt)
                    continue

                async def _send():
                    entity = await client.get_entity(target)
                    result = await client.send_message(entity, message)
                    return entity, result

                entity, result = self._run_async(_send(), timeout=20)
                return {
                    'status': 'success',
                    'result': result,
                    'telegram_message_id': result.id,
                    'telegram_user_id': entity.id if hasattr(entity, 'id') else None
                }
            except FloodWaitError as e:
                wait = min(e.seconds, 120)
                logger.warning('FloodWait %ds for account %s', wait, account.phone, extra=extra_base)
                time.sleep(wait)
                continue
            except Exception as e:
                is_transient, label = self._classify_error(e)
                logger.warning('Send attempt %d/%d failed: %s — %s',
                               attempt, retries, label, str(e), extra=extra_base)
                if not is_transient or attempt == retries:
                    return {'status': 'error', 'message': str(e),
                            'permanent': not is_transient, 'attempt': attempt, 'error_type': label}
                time.sleep(base_delay * (2 ** (attempt - 1)))

        return {'status': 'error', 'message': 'All retries exhausted',
                'permanent': True, 'attempt': retries}

    def fetch_dialogs_sync(self, account):
        async def _fetch():
            client = await self._ensure_connected(account)
            if not client:
                return []
            return await client.get_dialogs(limit=None)
        try:
            return self._run_async(_fetch(), timeout=30)
        except Exception:
            return []

    def fetch_dialog_messages_sync(self, account, dialog, min_id=0):
        async def _fetch():
            client = await self._ensure_connected(account)
            if not client:
                return []
            msgs = []
            async for msg in client.iter_messages(dialog, limit=20, min_id=min_id):
                if not msg.out:
                    msgs.append(msg)
            return msgs
        try:
            return self._run_async(_fetch(), timeout=20)
        except Exception:
            return []
