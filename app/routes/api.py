from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Campaign, Recipient
from app.services.campaign_service import get_campaign_stats

api_bp = Blueprint('api', __name__)

@api_bp.route('/campaign/<int:campaign_id>/status')
@login_required
def campaign_status(campaign_id):
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign: return jsonify({'error': 'Not found'}), 404
    stats = get_campaign_stats(campaign_id)
    return jsonify({'id': campaign.id, 'status': campaign.status, **stats})

@api_bp.route('/campaign/<int:campaign_id>/recipients')
@login_required
def campaign_recipients(campaign_id):
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    recipients = db.session.query(Recipient).filter_by(campaign_id=campaign_id).order_by(Recipient.id).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'items': [{
            'id': r.id, 'username': r.username, 'user_id': r.user_id,
            'status': r.status, 'last_error': r.last_error,
            'assigned_account': r.account.phone if r.assigned_account_id else None,
        } for r in recipients.items],
        'total': recipients.total, 'pages': recipients.pages, 'current_page': page,
    })
