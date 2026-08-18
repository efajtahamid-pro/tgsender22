import threading
import time
import logging
import random
from datetime import datetime, timedelta
from app.extensions import db, socketio
from app.models import Campaign, Recipient, TelegramAccount, Conversation, Message
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

    def _run(self, app):
        with app.app_context():
            self.telegram.init_all_clients()
            
            reply_poll_interval = app.config.get('REPLY_POLL_INTERVAL_SECONDS', 30)
            last_reply_poll = datetime.utcnow() - timedelta(seconds=reply_poll_interval)
            last_daily_reset = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            while self.running:
                try:
                    # 1. Process Campaigns
                    self._process_campaigns()
                    
                    # 2. Reset Daily Counters ( Midnight UTC )
                    now = datetime.utcnow()
                    if now.date() > last_daily_reset.date():
                        self._reset_daily_counters()
                        last_daily_reset = now

                    # 3. Poll Replies
                    if (now - last_reply_poll).total_seconds() >= reply_poll_interval:
                        self._poll_replies()
                        last_reply_poll = now

                    time.sleep(5)
                except Exception as e:
                    logger.exception("Background loop error")
                    time.sleep(10)

    def _process_campaigns(self):
        campaigns = db.session.query(Campaign).filter_by(status='running').all()
        for campaign in campaigns:
            self._send_batch(campaign)

    def _send_batch(self, campaign):
        batch_size = current_app.config.get('CAMPAIGN_BATCH_SIZE', 50)
        delay = campaign.delay_seconds or 2

        pending = db.session.query(Recipient).filter_by(campaign_id=campaign.id, status='pending').limit(batch_size).all()

        if not pending:
            campaign.status = 'completed'
            campaign.completed_at = datetime.utcnow()
            
            # Unassign accounts when campaign finishes
            campaign.selected_accounts = []
            
            db.session.commit()
            socketio.emit('campaign_status', {'campaign_id': campaign.id, 'status': 'completed'}, room=f'campaign_{campaign.id}')
            return

        # Use only the accounts selected for this specific campaign
        accounts = [acc for acc in campaign.selected_accounts if acc.is_active and acc.is_verified and acc.is_healthy]

        if not accounts:
            campaign.status = 'paused'
            db.session.commit()
            socketio.emit('campaign_status', {'campaign_id': campaign.id, 'status': 'paused', 'reason': 'no_active_accounts_selected'}, room=f'campaign_{campaign.id}')
            logger.warning("No active accounts selected for campaign %s. Pausing.", campaign.id)
            return

        # Prepare message variations
        messages = [m.strip() for m in campaign.message.splitlines() if m.strip()]
        if not messages:
            messages = [campaign.message]

        for recipient in pending:
            if campaign.status != 'running':
                break

            account = self._get_available_account(accounts)
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # FEATURE IMPLEMENTATION:
            # If no account is available (all hit daily limit), DO NOT pause or fail.
            # Just return. The remaining recipients stay 'pending'.
            # The midnight reset will make accounts available again to continue sending.
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            if not account:
                logger.info(f"Campaign {campaign.id}: All selected accounts reached their daily limit. Waiting for reset to continue sending.")
                return

            # Pick a random message variation
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

        target = recipient.username.lstrip('@') if recipient.username else None
        if not target:
            self._dead_letter(recipient, 'No username provided')
            return

        for attempt in range(1, max_retries + 1):
            result = self.telegram.send_message_sync(
                account=account, recipient_id=target, message=message_content,
                campaign_id=campaign.id, recipient_db_id=recipient.id,
            )

            if result['status'] == 'success':
                recipient.status = 'sent'
                recipient.sent_at = datetime.utcnow()
                recipient.assigned_account_id = account.id
                recipient.user_id = result.get('telegram_user_id')

                account.daily_sent += 1
                account.total_sent += 1
                account.last_used = datetime.utcnow()

                if not recipient.conversation:
                    conv = Conversation(recipient_id=recipient.id, campaign_id=campaign.id, employee_id=campaign.employee_id)
                    db.session.add(conv)

                socketio.emit('recipient_update', {
                    'campaign_id': campaign.id, 'recipient_id': recipient.id,
                    'status': 'sent', 'assigned_account': account.phone
                }, room=f'campaign_{campaign.id}')
                return

            is_permanent = result.get('permanent', False)
            recipient.last_error = result.get('message', 'Unknown error')
            recipient.retry_count = attempt

            if is_permanent or attempt >= max_retries:
                self._dead_letter(recipient, recipient.last_error)
                socketio.emit('recipient_update', {
                    'campaign_id': campaign.id, 'recipient_id': recipient.id,
                    'status': 'dead_letter', 'last_error': recipient.last_error
                }, room=f'campaign_{campaign.id}')
                return

            backoff = base_delay * (2 ** (attempt - 1))
            time.sleep(backoff)

    def _dead_letter(self, recipient, reason):
        recipient.status = 'dead_letter'
        recipient.dead_letter_reason = reason
        recipient.dead_lettered_at = datetime.utcnow()
        logger.error('Message moved to dead-letter queue', extra={'campaign_id': recipient.campaign_id, 'recipient_id': recipient.id, 'status': 'dead_letter'})

    def _reset_daily_counters(self):
        logger.info("Resetting daily counters for all accounts...")
        accounts = db.session.query(TelegramAccount).filter_by(is_active=True).all()
        now = datetime.utcnow()
        for acc in accounts:
            acc.daily_sent = 0
            acc.last_reset = now
        db.session.commit()

    def _poll_replies(self):
        accounts = db.session.query(TelegramAccount).filter_by(is_active=True, is_verified=True).all()
        for account in accounts:
            try:
                self._poll_account(account)
            except Exception as e:
                logger.exception("Reply poll failed for account %s", account.phone)

    def _poll_account(self, account):
        last_msg = db.session.query(Message.telegram_message_id).join(Conversation, Message.conversation_id == Conversation.id).join(Recipient, Conversation.recipient_id == Recipient.id).filter(Recipient.assigned_account_id == account.id).filter(Message.sender == 'recipient').order_by(Message.telegram_message_id.desc()).first()
        since_id = last_msg[0] if last_msg else 0

        new_messages = self.telegram.fetch_new_replies_sync(account, since_id=since_id)
        if not new_messages:
            return

        recipients = db.session.query(Recipient).filter(Recipient.assigned_account_id == account.id).filter(Recipient.user_id.isnot(None)).all()
        peer_to_recipient = {str(r.user_id): r for r in recipients}

        for msg in new_messages:
            peer_id = str(msg.get('peer_id') or '')
            recipient = peer_to_recipient.get(peer_id)
            if not recipient:
                continue

            conv = recipient.conversation
            if not conv:
                conv = Conversation(recipient_id=recipient.id, campaign_id=recipient.campaign_id, employee_id=recipient.campaign.employee_id)
                db.session.add(conv)
                db.session.flush()

            existing = db.session.query(Message).filter_by(telegram_message_id=msg['id']).first()
            if existing:
                continue

            db_msg = Message(
                conversation_id=conv.id, sender='recipient', content=msg['text'],
                telegram_message_id=msg['id'],
                timestamp=msg['date'].replace(tzinfo=None) if hasattr(msg['date'], 'tzinfo') else datetime.utcnow(),
            )
            db.session.add(db_msg)

            conv.unread_count = (conv.unread_count or 0) + 1
            conv.last_message_at = datetime.utcnow()
            recipient.status = 'replied'
            recipient.replied_at = datetime.utcnow()
            db.session.commit()

            socketio.emit('new_reply', {
                'conversation_id': conv.id, 'recipient_id': recipient.id,
                'campaign_id': recipient.campaign_id, 'employee_id': conv.employee_id,
                'unread_count': conv.unread_count, 'content': msg['text'],
                'sender': 'recipient', 'timestamp': db_msg.timestamp.isoformat(),
            }, room=f'user_{conv.employee_id}')

            socketio.emit('recipient_update', {
                'campaign_id': recipient.campaign_id, 'recipient_id': recipient.id,
                'status': 'replied',
            }, room=f'campaign_{recipient.campaign_id}')

    def send_employee_reply(self, conv_id, content, employee_id):
        conv = db.session.get(Conversation, conv_id)
        if not conv or conv.employee_id != employee_id:
            return False, 'Unauthorized'

        recipient = conv.recipient
        account = recipient.account
        if not account:
            return False, 'No sending account found'

        if not recipient.user_id:
            return False, 'Cannot reply: Recipient user_id is missing (initial message may have failed)'

        target = recipient.user_id
        result = self.telegram.send_message_sync(
            account=account, recipient_id=target, message=content,
            campaign_id=recipient.campaign_id, recipient_db_id=recipient.id,
        )

        if result['status'] == 'success':
            msg = Message(conversation_id=conv_id, sender='employee', content=content, telegram_message_id=result.get('telegram_message_id'))
            db.session.add(msg)
            conv.last_message_at = datetime.utcnow()
            db.session.commit()

            socketio.emit('new_reply', {
                'conversation_id': conv_id, 'sender': 'employee', 'content': content,
                'timestamp': msg.timestamp.isoformat(),
            }, room=f'user_{conv.employee_id}')
            return True, 'Sent'
        else:
            return False, result.get('message', 'Send failed')
