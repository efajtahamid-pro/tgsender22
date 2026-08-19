import threading
import time
import logging
import random
from datetime import datetime, timedelta
from app.extensions import db, socketio
from app.models import (
    Campaign, Recipient, TelegramAccount, Conversation, Message,
    ReplyCheckpoint, SendLog, WorkerHeartbeat
)
from app.services.telegram_service import TelegramService
from flask import current_app

logger = logging.getLogger(__name__)


class BackgroundSender:
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
        self.running = False
        self.thread = None
        self.telegram = TelegramService()
        self._initialized = True

    def start(self, app):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, args=(app,), daemon=True)
        self.thread.start()
        logger.info("Background sender thread started.")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Background sender stopped.")

    def _run(self, app):
        with app.app_context():
            logger.info("Initializing Telegram clients for all active accounts...")
            self.telegram.init_all_clients()
            logger.info("Client initialization complete. Entering main loop.")

            poll_interval = current_app.config.get('REPLY_POLL_INTERVAL_SECONDS', 30)
            last_poll_time = 0

            while self.running:
                try:
                    self._reset_stuck_recipients()
                    self._reset_daily_limits()
                    self._process_campaigns()

                    if time.time() - last_poll_time >= poll_interval:
                        self._poll_replies()
                        last_poll_time = time.time()

                    time.sleep(5)
                    db.session.remove()
                except Exception as e:
                    logger.exception("Background loop error: %s", e)
                    db.session.rollback()
                    db.session.remove()
                    time.sleep(10)

    def _update_heartbeat(self, name, status, error=None):
        try:
            hb = db.session.query(WorkerHeartbeat).filter_by(worker_name=name).first()
            if hb:
                hb.last_heartbeat = datetime.utcnow()
                hb.status = status
                hb.last_error = error
            else:
                hb = WorkerHeartbeat(
                    worker_name=name,
                    worker_type='campaign' if 'campaign' in name else 'reply',
                    last_heartbeat=datetime.utcnow(),
                    status=status,
                    last_error=error
                )
                db.session.add(hb)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("Heartbeat write failed: %s", e)

    def _reset_stuck_recipients(self):
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        stuck = db.session.query(Recipient).filter_by(status='sending').all()
        reset_count = 0
        for r in stuck:
            last_log = db.session.query(SendLog).filter_by(recipient_id=r.id) \
                .order_by(SendLog.started_at.desc()).first()
            if not last_log or not last_log.started_at or last_log.started_at < cutoff:
                r.status = 'pending'
                reset_count += 1
        if reset_count > 0:
            db.session.commit()
            logger.info("Reset %d stuck recipients (>5min in sending state).", reset_count)

    def _reset_daily_limits(self):
        now = datetime.utcnow()
        for camp in db.session.query(Campaign).filter(Campaign.status.in_(['paused', 'running'])).all():
            if camp.last_reset and (now - camp.last_reset) >= timedelta(hours=24):
                camp.daily_sent = 0
                camp.last_reset = now
                if camp.status == 'paused' and camp.pause_reason == 'daily_limit_reached':
                    camp.status = 'running'
                    camp.pause_reason = None
                    logger.info("Campaign %s daily limit reset — auto-resuming.", camp.id)
        db.session.commit()

    def _process_campaigns(self):
        self._update_heartbeat('campaign_worker', 'running')
        campaigns = db.session.query(Campaign).filter_by(status='running').all()
        for campaign in campaigns:
            self._send_batch(campaign)

    def _send_batch(self, campaign):
        batch_size = current_app.config.get('CAMPAIGN_BATCH_SIZE', 50)
        delay = campaign.delay_seconds or 2

        pending = db.session.query(Recipient).filter_by(
            campaign_id=campaign.id, status='pending'
        ).limit(batch_size).all()

        if not pending:
            still_sending = db.session.query(Recipient).filter_by(
                campaign_id=campaign.id, status='sending'
            ).first()
            if not still_sending:
                campaign.status = 'completed'
                campaign.completed_at = datetime.utcnow()
                db.session.commit()
                socketio.emit('campaign_status', {
                    'campaign_id': campaign.id, 'status': 'completed'
                }, room=f'campaign_{campaign.id}')
            return

        # FIX: Removed the health_status check. We will attempt to send with any active/verified account.
        # If the account is actually disconnected, send_message_sync will handle the error and retry/dead-letter.
        accounts = [acc for acc in campaign.selected_accounts
                     if acc.is_active and acc.is_verified]

        if not accounts:
            campaign.status = 'paused'
            campaign.pause_reason = 'no_active_accounts'
            db.session.commit()
            socketio.emit('campaign_status', {
                'campaign_id': campaign.id, 'status': 'paused',
                'reason': 'no_active_accounts'
            }, room=f'campaign_{campaign.id}')
            logger.warning("Campaign %s paused — no active/verified accounts.", campaign.id)
            return

        messages = [m.strip() for m in campaign.message.split('---') if m.strip()]
        if not messages:
            messages = [campaign.message]

        for recipient in pending:
            if campaign.status != 'running':
                break
            if campaign.daily_sent >= campaign.daily_limit:
                campaign.status = 'paused'
                campaign.pause_reason = 'daily_limit_reached'
                db.session.commit()
                socketio.emit('campaign_status', {
                    'campaign_id': campaign.id, 'status': 'paused',
                    'reason': 'daily_limit_reached'
                }, room=f'campaign_{campaign.id}')
                return

            account = self._get_available_account(accounts)
            if not account:
                campaign.status = 'paused'
                campaign.pause_reason = 'rate_limit_reached'
                db.session.commit()
                socketio.emit('campaign_status', {
                    'campaign_id': campaign.id, 'status': 'paused',
                    'reason': 'rate_limit_reached'
                }, room=f'campaign_{campaign.id}')
                return

            recipient.status = 'sending'
            db.session.commit()

            chosen_message = random.choice(messages)
            self._send_to_recipient(account, recipient, campaign, chosen_message)
            db.session.commit()
            time.sleep(delay)

    def _get_available_account(self, accounts):
        now = datetime.utcnow()
        for acc in accounts:
            if acc.last_reset and (now - acc.last_reset) >= timedelta(hours=24):
                acc.daily_sent = 0
                acc.last_reset = now
            if acc.daily_sent < acc.daily_limit:
                return acc
        return None

    def _send_to_recipient(self, account, recipient, campaign, message_content):
        max_retries = current_app.config.get('MAX_RETRY_ATTEMPTS', 3)
        base_delay = current_app.config.get('RETRY_BASE_DELAY', 2.0)

        if recipient.user_id:
            target = recipient.user_id
        elif recipient.username:
            target = recipient.username.lstrip('@').lower()
        else:
            recipient.status = 'dead_letter'
            recipient.dead_letter_reason = 'No username provided'
            recipient.dead_lettered_at = datetime.utcnow()
            return

        log_entry = SendLog(
            campaign_id=campaign.id, recipient_id=recipient.id,
            account_id=account.id, proxy_id=account.proxy_id,
            attempt=recipient.retry_count + 1, status='sending'
        )
        db.session.add(log_entry)
        db.session.commit()

        result = self.telegram.send_message_sync(
            account, target, message_content,
            retries=max_retries, base_delay=base_delay,
            campaign_id=campaign.id, recipient_db_id=recipient.id
        )
        log_entry.completed_at = datetime.utcnow()

        if result['status'] == 'success':
            recipient.status = 'sent'
            recipient.sent_at = datetime.utcnow()
            recipient.assigned_account_id = account.id
            recipient.user_id = result.get('telegram_user_id') or recipient.user_id
            account.daily_sent += 1
            campaign.daily_sent += 1
            account.last_used = datetime.utcnow()
            account.total_sent = (account.total_sent or 0) + 1
            log_entry.status = 'success'
            log_entry.telegram_message_id = result.get('telegram_message_id')

            if not recipient.conversation:
                db.session.add(Conversation(
                    recipient_id=recipient.id,
                    campaign_id=campaign.id,
                    employee_id=campaign.employee_id
                ))
                db.session.flush()

            socketio.emit('recipient_update', {
                'campaign_id': campaign.id,
                'recipient_id': recipient.id,
                'status': 'sent',
                'assigned_account': account.phone
            }, room=f'campaign_{campaign.id}')

        else:
            recipient.retry_count += 1
            recipient.last_error = result.get('message')
            log_entry.status = 'failed'
            log_entry.error = recipient.last_error

            if result.get('permanent') or recipient.retry_count >= max_retries:
                recipient.status = 'dead_letter'
                recipient.dead_letter_reason = recipient.last_error
                recipient.dead_lettered_at = datetime.utcnow()
                socketio.emit('recipient_update', {
                    'campaign_id': campaign.id,
                    'recipient_id': recipient.id,
                    'status': 'dead_letter',
                    'last_error': recipient.last_error
                }, room=f'campaign_{campaign.id}')
            else:
                recipient.status = 'pending'

    def _poll_replies(self):
        self._update_heartbeat('reply_worker', 'running')
        accounts = db.session.query(TelegramAccount).filter_by(
            is_active=True, is_verified=True
        ).all()
        for account in accounts:
            try:
                self._poll_account(account)
            except Exception as e:
                logger.exception("Reply poll failed for account %s: %s", account.phone, e)

    def _poll_account(self, account):
        dialogs = self.telegram.fetch_dialogs_sync(account)
        if not dialogs:
            return

        # FIX: Fetch all campaigns assigned to this account, not just running ones.
        campaigns = db.session.query(Campaign).filter(
            Campaign.selected_accounts.any(id=account.id)
        ).all()
        campaign_ids = [c.id for c in campaigns]

        # FIX: Fetch recipients for these campaigns regardless of assigned_account_id.
        # This allows receiving messages even if the initial campaign message failed to send.
        recipients = db.session.query(Recipient).filter(
            Recipient.campaign_id.in_(campaign_ids)
        ).all()
        
        # FIX: Map by BOTH user_id and username to catch messages from unsent recipients
        peer_to_recipient = {}
        username_to_recipient = {}
        for r in recipients:
            if r.user_id:
                peer_to_recipient[str(r.user_id)] = r
            if r.username:
                username_to_recipient[r.username.lower().lstrip('@')] = r

        for dialog in dialogs:
            checkpoint = db.session.query(ReplyCheckpoint).filter_by(
                account_id=account.id, dialog_id=dialog.id
            ).first()
            min_id = checkpoint.last_message_id if checkpoint else 0

            messages = self.telegram.fetch_dialog_messages_sync(account, dialog, min_id=min_id)
            if not messages:
                continue

            for msg in messages:
                sender_id_str = str(msg.sender_id) if msg.sender_id else None
                sender_username = None
                
                # Try to extract username from the message sender
                if hasattr(msg, 'sender') and msg.sender and hasattr(msg.sender, 'username') and msg.sender.username:
                    sender_username = msg.sender.username.lower().lstrip('@')
                
                recipient = None
                if sender_id_str:
                    recipient = peer_to_recipient.get(sender_id_str)
                
                if not recipient and sender_username:
                    recipient = username_to_recipient.get(sender_username)
                
                if not recipient:
                    continue # Message from someone not in our recipient list
                
                # FIX: If we found them by username but didn't have their user_id, save it!
                if not recipient.user_id and sender_id_str:
                    try:
                        recipient.user_id = int(msg.sender_id)
                    except ValueError:
                        pass
                
                # FIX: If they aren't assigned to this account yet, assign them now
                if not recipient.assigned_account_id:
                    recipient.assigned_account_id = account.id

                conv = db.session.query(Conversation).filter_by(recipient_id=recipient.id).first()
                if not conv:
                    conv = Conversation(
                        recipient_id=recipient.id,
                        campaign_id=recipient.campaign_id,
                        employee_id=recipient.campaign.employee_id
                    )
                    db.session.add(conv)
                    db.session.flush()

                if db.session.query(Message).filter_by(telegram_message_id=msg.id).first():
                    continue

                msg_date = msg.date.replace(tzinfo=None) if hasattr(msg.date, 'tzinfo') and msg.date.tzinfo else datetime.utcnow()
                db_msg = Message(
                    conversation_id=conv.id,
                    sender='recipient',
                    content=msg.text or '[Media]',
                    telegram_message_id=msg.id,
                    timestamp=msg_date
                )
                db.session.add(db_msg)

                conv.unread_count += 1
                conv.last_message_at = datetime.utcnow()
                recipient.status = 'replied'
                recipient.replied_at = datetime.utcnow()

                if not checkpoint:
                    checkpoint = ReplyCheckpoint(
                        account_id=account.id,
                        dialog_id=dialog.id,
                        last_message_id=msg.id
                    )
                    db.session.add(checkpoint)
                else:
                    checkpoint.last_message_id = max(checkpoint.last_message_id, msg.id)

                db.session.commit()

                socketio.emit('new_reply', {
                    'conversation_id': conv.id,
                    'employee_id': conv.employee_id,
                    'content': msg.text or '[Media]',
                    'sender': 'recipient',
                    'timestamp': db_msg.timestamp.isoformat()
                }, room=f'user_{conv.employee_id}')
                socketio.emit('recipient_update', {
                    'campaign_id': recipient.campaign_id,
                    'recipient_id': recipient.id,
                    'status': 'replied'
                }, room=f'campaign_{recipient.campaign_id}')

    def send_employee_reply(self, conv_id, content, employee_id):
        conv = db.session.get(Conversation, conv_id)
        if not conv or conv.employee_id != employee_id:
            return False, 'Unauthorized'

        recipient = conv.recipient
        account = recipient.account
        if not account:
            return False, 'No sending account found'
            
        target = recipient.user_id
        if not target and recipient.username:
            target = recipient.username.lstrip('@').lower()
            
        if not target:
            return False, 'Cannot reply: Recipient has no username or user_id'

        result = self.telegram.send_message_sync(
            account, target, content,
            campaign_id=recipient.campaign_id, recipient_db_id=recipient.id
        )

        if result['status'] == 'success':
            msg = Message(
                conversation_id=conv_id,
                sender='employee',
                content=content,
                telegram_message_id=result.get('telegram_message_id')
            )
            db.session.add(msg)
            conv.last_message_at = datetime.utcnow()
            db.session.commit()
            socketio.emit('new_reply', {
                'conversation_id': conv_id,
                'sender': 'employee',
                'content': content,
                'timestamp': msg.timestamp.isoformat()
            }, room=f'user_{conv.employee_id}')
            return True, 'Sent'
        return False, result.get('message', 'Send failed')
