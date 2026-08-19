from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, current_app)
from flask_login import login_required, current_user
from datetime import datetime
from app.extensions import db, socketio
from app.models import Campaign, Conversation, Message, Recipient, User
from app.services.campaign_service import get_campaign_stats
from app.services.telegram_service import TelegramService

employee_bp = Blueprint('employee', __name__)


@employee_bp.before_request
@login_required
def check_employee():
    # FIX: Allow Admins full access to the employee dashboard to handle unassigned chats
    if current_user.role not in ('employee', 'admin'):
        flash('Access required', 'warning')
        return redirect(url_for('admin.dashboard'))


@employee_bp.route('/dashboard')
def dashboard():
    # FIX: If admin, show all active conversations. If employee, show only theirs.
    if current_user.role == 'admin':
        conversations = db.session.query(Conversation).filter_by(is_active=True) \
            .order_by(Conversation.last_message_at.desc()).all()
        campaigns = db.session.query(Campaign).order_by(Campaign.created_at.desc()).all()
    else:
        conversations = db.session.query(Conversation).filter_by(
            employee_id=current_user.id, is_active=True
        ).order_by(Conversation.last_message_at.desc()).all()
        campaigns = db.session.query(Campaign).filter_by(employee_id=current_user.id) \
            .order_by(Campaign.created_at.desc()).all()

    total_unread = sum(c.unread_count for c in conversations)
    return render_template(
        'employee/dashboard.html',
        campaigns=campaigns,
        conversations=conversations,
        total_unread=total_unread
    )


@employee_bp.route('/campaign/<int:id>')
def campaign_detail(id):
    campaign = db.session.get(Campaign, id)
    if not campaign or (campaign.employee_id != current_user.id and current_user.role != 'admin'):
        flash('Campaign not found or not assigned to you.', 'danger')
        return redirect(url_for('employee.dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 100, type=int)
    recipients_pag = db.session.query(Recipient).filter_by(campaign_id=id) \
        .order_by(Recipient.id).paginate(page=page, per_page=per_page, error_out=False)

    stats = get_campaign_stats(id)
    return render_template(
        'employee/campaign_detail.html',
        campaign=campaign,
        recipients=recipients_pag.items,
        pagination=recipients_pag,
        stats=stats
    )


@employee_bp.route('/conversations')
def conversations():
    # FIX: Admins fetch all conversations, employees fetch theirs
    if current_user.role == 'admin':
        convs = db.session.query(Conversation).filter_by(is_active=True) \
            .order_by(Conversation.last_message_at.desc()).all()
    else:
        convs = db.session.query(Conversation).filter_by(
            employee_id=current_user.id, is_active=True
        ).order_by(Conversation.last_message_at.desc()).all()
        
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
    # FIX: Admins can read any conversation
    if not conv or (conv.employee_id != current_user.id and current_user.role != 'admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    msgs = db.session.query(Message).filter_by(conversation_id=conv_id) \
        .order_by(Message.timestamp.asc()).all()
    return jsonify([{
        'id': m.id, 'sender': m.sender, 'content': m.content,
        'timestamp': m.timestamp.strftime('%Y-%m-%d %H:%M'),
    } for m in msgs])


@employee_bp.route('/conversation/<int:conv_id>/send', methods=['POST'])
def send_reply(conv_id):
    conv = db.session.get(Conversation, conv_id)
    # FIX: Admins can reply to any conversation
    if not conv or (conv.employee_id != current_user.id and current_user.role != 'admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    content = request.json.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Empty message'}), 400

    recipient = conv.recipient
    account = recipient.account
    if not account:
        return jsonify({'error': 'No sending account found'}), 400
    
    target = recipient.user_id
    if not target and recipient.username:
        target = recipient.username.lstrip('@').lower()
        
    if not target:
        return jsonify({'error': 'Recipient has no username or user_id. Cannot send.'}), 400

    tg = TelegramService()
    result = tg.send_message_sync(
        account=account, target=target, message=content,
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
        return jsonify({'status': 'sent', 'message': 'Sent'})
    return jsonify({'error': result.get('message', 'Send failed')}), 500


@employee_bp.route('/conversation/<int:conv_id>/mark-read', methods=['POST'])
def mark_read(conv_id):
    conv = db.session.get(Conversation, conv_id)
    # FIX: Admins can mark any conversation as read
    if not conv or (conv.employee_id != current_user.id and current_user.role != 'admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    conv.unread_count = 0
    db.session.commit()
    return jsonify({'status': 'success'})
