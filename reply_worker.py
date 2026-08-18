import os
import time
import logging
from datetime import datetime
from app import create_app
from app.extensions import db, socketio
from app.models import TelegramAccount, Recipient, Conversation, Message, ReplyCheckpoint
from app.services.telegram_service import TelegramService
from sqlalchemy import text

logger = logging.getLogger(__name__)
app = create_app(os.getenv('APP_ENV', 'development'))

def update_heartbeat(status, error=None):
    db.session.execute(
        text("""
            INSERT INTO worker_heartbeats (worker_name, worker_type, last_heartbeat, status, last_error)
            VALUES ('reply_worker_1', 'reply', NOW(), :status, :error)
            ON CONFLICT (worker_name) DO UPDATE SET
                last_heartbeat = NOW(),
                status = :status,
                last_error = :error
        """),
        {'status': status, 'error': error}
    )
    db.session.commit()

def run():
    telegram = TelegramService()
    with app.app_context():
        telegram.init_all_clients()
        logger.info("Reply Worker started.")
        while True:
            try:
                update_heartbeat('running')
                accounts = db.session.query(TelegramAccount).filter_by(is_active=True, is_verified=True, health_status='healthy').all()
                
                for account in accounts:
                    dialogs = telegram.fetch_dialogs_sync(account)
                    if not dialogs: continue
                    
                    # Map recipients by telegram user_id for this account
                    recipients = db.session.query(Recipient).filter(
                        Recipient.assigned_account_id == account.id,
                        Recipient.user_id.isnot(None)
                    ).all()
                    peer_to_recipient = {str(r.user_id): r for r in recipients}

                    for dialog in dialogs:
                        dialog_id = dialog.id
                        checkpoint = db.session.query(ReplyCheckpoint).filter_by(
                            account_id=account.id, dialog_id=dialog_id
                        ).first()
                        min_id = checkpoint.last_message_id if checkpoint else 0

                        messages = telegram.fetch_dialog_messages_sync(account, dialog, min_id=min_id)
                        if not messages: continue

                        for msg in messages:
                            if msg.out: continue
                            
                            # Robust Peer Matching
                            sender_id = str(msg.sender_id) if msg.sender_id else str(dialog.id)
                            recipient = peer_to_recipient.get(sender_id)
                            if not recipient: continue

                            conv = recipient.conversation
                            if not conv:
                                conv = Conversation(recipient_id=recipient.id, campaign_id=recipient.campaign_id, employee_id=recipient.campaign.employee_id)
                                db.session.add(conv)
                                db.session.flush()

                            # Deduplication via DB Unique Constraint
                            exists = db.session.query(Message).filter_by(telegram_message_id=msg.id).first()
                            if exists: continue

                            db_msg = Message(
                                conversation_id=conv.id,
                                sender='recipient',
                                content=msg.text or '',
                                telegram_message_id=msg.id,
                                timestamp=msg.date.replace(tzinfo=None) if hasattr(msg.date, 'tzinfo') else datetime.utcnow()
                            )
                            db.session.add(db_msg)

                            conv.unread_count += 1
                            conv.last_message_at = datetime.utcnow()
                            recipient.status = 'replied'
                            recipient.replied_at = datetime.utcnow()

                            if not checkpoint:
                                checkpoint = ReplyCheckpoint(account_id=account.id, dialog_id=dialog_id, last_message_id=msg.id)
                                db.session.add(checkpoint)
                            else:
                                checkpoint.last_message_id = max(checkpoint.last_message_id, msg.id)

                            db.session.commit()

                            socketio.emit('new_reply', {
                                'conversation_id': conv.id,
                                'employee_id': conv.employee_id,
                                'content': msg.text or '',
                                'sender': 'recipient',
                                'timestamp': db_msg.timestamp.isoformat()
                            }, room=f'user_{conv.employee_id}')

                time.sleep(15) # Poll every 15 seconds
            except Exception as e:
                logger.exception("Reply worker crashed")
                update_heartbeat('error', str(e))
                time.sleep(30)

if __name__ == '__main__':
    run()
