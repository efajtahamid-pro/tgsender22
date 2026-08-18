from flask import (Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app)
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Campaign, Conversation, Message, Recipient
from app.services.campaign_service import get_campaign_stats

employee_bp = Blueprint('employee', __name__)

@employee_bp.before_request
@login_required
def check_employee():
    # Allow both employees and admins to access employee routes
    if current_user.role not in ('employee', 'admin'):
        flash('Access required', 'warning')
        return redirect(url_for('admin.dashboard'))

@employee_bp.route('/dashboard')
def dashboard():
    campaigns = db.session.query(Campaign).filter_by(employee_id=current_user.id).order_by(Campaign.created_at.desc()).all()
    conversations = db.session.query(Conversation).filter_by(employee_id=current_user.id, is_active=True).order_by(Conversation.last_message_at.desc()).all()
    total_unread = sum(c.unread_count for c in conversations)
    
    return render_template('employee/dashboard.html', campaigns=campaigns, conversations=conversations, total_unread=total_unread)

@employee_bp.route('/campaign/<int:id>')
def campaign_detail(id):
    campaign = db.session.get(Campaign, id)
    if not campaign or campaign.employee_id != current_user.id:
        flash('Campaign not found or not assigned to you.', 'danger')
        return redirect(url_for('employee.dashboard'))
    
    recipients = campaign.recipients.limit(500).all()
    stats = get_campaign_stats(id)
    return render_template('employee/campaign_detail.html', campaign=campaign, recipients=recipients, stats=stats)

@employee_bp.route('/conversations')
def conversations():
    convs = db.session.query(Conversation).filter_by(employee_id=current_user.id, is_active=True).order_by(Conversation.last_message_at.desc()).all()
    return jsonify([{
        'id': c.id,
        'recipient_username': c.recipient.username,
        'unread_count': c.unread_count,
        'last_message_at': c.last_message_at.isoformat() if c.last_message_at else None,
        'campaign_name': c.campaign.name,
    } for c in convs])

@employee_bp.route('/conversation/<int:conv_id>/messages')
def get_messages(conv_id):
    conv = db.session.get(Conversation, conv_id)
    if not conv or conv.employee_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    msgs = db.session.query(Message).filter_by(conversation_id=conv_id).order_by(Message.timestamp.asc()).all()
    return jsonify([{
        'id': m.id,
        'sender': m.sender,
        'content': m.content,
        'timestamp': m.timestamp.strftime('%Y-%m-%d %H:%M'),
    } for m in msgs])

@employee_bp.route('/conversation/<int:conv_id>/send', methods=['POST'])
def send_reply(conv_id):
    conv = db.session.get(Conversation, conv_id)
    if not conv or conv.employee_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    content = request.json.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Empty message'}), 400

    sender = current_app.bg_sender
    success, message = sender.send_employee_reply(conv_id, content, current_user.id)

    if success:
        return jsonify({'status': 'sent', 'message': message})
    else:
        return jsonify({'error': message}), 500

@employee_bp.route('/conversation/<int:conv_id>/mark-read', methods=['POST'])
def mark_read(conv_id):
    conv = db.session.get(Conversation, conv_id)
    if not conv or conv.employee_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    conv.unread_count = 0
    db.session.commit()
    return jsonify({'status': 'success'})
