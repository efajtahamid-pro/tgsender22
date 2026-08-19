import logging
from app.extensions import db
from app.models import Campaign, Recipient

logger = logging.getLogger(__name__)

def get_campaign_stats(campaign_id):
    recipients = db.session.query(Recipient).filter_by(campaign_id=campaign_id).all()
    total = len(recipients)
    sent = sum(1 for r in recipients if r.status == 'sent')
    failed = sum(1 for r in recipients if r.status == 'failed')
    pending = sum(1 for r in recipients if r.status == 'pending')
    replied = sum(1 for r in recipients if r.status == 'replied')
    dead_letter = sum(1 for r in recipients if r.status == 'dead_letter')

    completed = sent + failed + replied + dead_letter
    progress = round((completed / total * 100), 1) if total > 0 else 0

    return {
        'total': total, 'sent': sent, 'failed': failed, 'pending': pending,
        'replied': replied, 'dead_letter': dead_letter, 'progress': progress,
    }
