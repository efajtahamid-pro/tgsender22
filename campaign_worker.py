import os
import time
import random
import logging
from datetime import datetime, timedelta
from app import create_app
from app.extensions import db
from app.models import Campaign, Recipient, TelegramAccount, Conversation, SendLog
from app.services.telegram_service import TelegramService
from sqlalchemy import text

logger = logging.getLogger(__name__)
app = create_app(os.getenv('APP_ENV', 'development'))

def update_heartbeat(status, error=None):
    beat = db.session.execute(
        text("""
            INSERT INTO worker_heartbeats (worker_name, worker_type, last_heartbeat, status, last_error)
            VALUES ('campaign_worker_1', 'campaign', NOW(), :status, :error)
            ON CONFLICT (worker_name) DO UPDATE SET
                last_heartbeat = NOW(),
                status = :status,
                last_error = :error
        """),
        {'status': status, 'error': error}
    )
    db.session.commit()

def reset_daily_limits():
    now = datetime.utcnow()
    campaigns = db.session.query(Campaign).filter(Campaign.status.in_(['paused', 'running'])).all()
    for camp in campaigns:
        if camp.last_reset and (now - camp.last_reset) >= timedelta(hours=24):
            camp.daily_sent = 0
            camp.last_reset = now
            if camp.status == 'paused' and camp.pause_reason == 'daily_limit_reached':
                camp.status = 'running'
                camp.pause_reason = None
    db.session.commit()

def claim_recipient(campaign_id):
    """Atomic DB-level claim to prevent duplicate sends across workers."""
    result = db.session.execute(
        text("""
            UPDATE recipients 
            SET status = 'sending'
            WHERE id = (
                SELECT id FROM recipients 
                WHERE campaign_id = :cid AND status = 'pending'
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, username
        """),
        {'cid': campaign_id}
    ).fetchone()
    db.session.commit()
    return result

def process_campaign(campaign, telegram):
    if campaign.daily_sent >= campaign.daily_limit:
        campaign.status = 'paused'
        campaign.pause_reason = 'daily_limit_reached'
        db.session.commit()
        return

    claimed = claim_recipient(campaign.id)
    if not claimed:
        # Check if fully completed
        if not db.session.query(Recipient).filter_by(campaign_id=campaign.id, status='pending').first():
            campaign.status = 'completed'
            campaign.completed_at = datetime.utcnow()
            db.session.commit()
        return

    rec_id, username = claimed
    recipient = db.session.get(Recipient, rec_id)
    
    # Select least recently used healthy account
    account = db.session.query(TelegramAccount).filter(
        TelegramAccount.id.in_([a.id for a in campaign.selected_accounts]),
        TelegramAccount.is_active == True,
        TelegramAccount.is_verified == True,
        TelegramAccount.health_status == 'healthy',
        TelegramAccount.daily_sent < TelegramAccount.daily_limit
    ).order_by(TelegramAccount.last_used.asc().nullsfirst()).first()

    if not account:
        recipient.status = 'pending' # Revert to pending
        db.session.commit()
        campaign.status = 'paused'
        campaign.pause_reason = 'no_healthy_accounts'
        db.session.commit()
        return

    log_entry = SendLog(campaign_id=campaign.id, recipient_id=recipient.id, account_id=account.id, proxy_id=account.proxy_id, attempt=recipient.retry_count + 1, status='sending')
    db.session.add(log_entry)
    db.session.commit()

    # Message Variations (split by '---')
    messages = [m.strip() for m in campaign.message.split('---') if m.strip()]
    chosen_message = random.choice(messages) if messages else campaign.message

    target = username.lstrip('@')
    result = telegram.send_message_sync(account, target, chosen_message)

    log_entry.completed_at = datetime.utcnow()
    if result['status'] == 'success':
        recipient.status = 'sent'
        recipient.sent_at = datetime.utcnow()
        recipient.assigned_account_id = account.id
        recipient.user_id = result.get('telegram_user_id')
        account.daily_sent += 1
        campaign.daily_sent += 1
        account.last_used = datetime.utcnow()
        log_entry.status = 'success'
        log_entry.telegram_message_id = result.get('telegram_message_id')
        
        if not recipient.conversation:
            conv = Conversation(recipient_id=recipient.id, campaign_id=campaign.id, employee_id=campaign.employee_id)
            db.session.add(conv)
    else:
        recipient.retry_count += 1
        recipient.last_error = result.get('message')
        log_entry.status = 'failed'
        log_entry.error = recipient.last_error
        
        if result.get('permanent') or recipient.retry_count >= 3:
            recipient.status = 'dead_letter'
            recipient.dead_letter_reason = recipient.last_error
        else:
            recipient.status = 'pending' # Retry later

    db.session.commit()
    time.sleep(campaign.delay_seconds or 2)

def run():
    telegram = TelegramService()
    with app.app_context():
        telegram.init_all_clients()
        logger.info("Campaign Worker started.")
        while True:
            try:
                update_heartbeat('running')
                reset_daily_limits()
                
                campaigns = db.session.query(Campaign).filter_by(status='running').all()
                for camp in campaigns:
                    process_campaign(camp, telegram)
                    
                time.sleep(2)
            except Exception as e:
                logger.exception("Campaign worker crashed")
                update_heartbeat('error', str(e))
                time.sleep(10)

if __name__ == '__main__':
    run()
