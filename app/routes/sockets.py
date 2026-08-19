import logging
from flask_login import current_user
from flask_socketio import join_room, leave_room, disconnect
from app.extensions import db
from app.models import Campaign

logger = logging.getLogger(__name__)

def register_socket_handlers(socketio):

    @socketio.on('connect')
    def on_connect():
        if not current_user.is_authenticated:
            return False
        logger.info('Socket connected', extra={'user_id': current_user.id})

    @socketio.on('disconnect')
    def on_disconnect():
        if current_user.is_authenticated:
            logger.info('Socket disconnected', extra={'user_id': current_user.id})

    @socketio.on('join_campaign')
    def on_join_campaign(data):
        campaign_id = data.get('campaign_id')
        if not campaign_id:
            return
        
        campaign = db.session.get(Campaign, campaign_id)
        if not campaign:
            return disconnect()

        if current_user.role == 'admin' or campaign.employee_id == current_user.id or campaign.created_by == current_user.id:
            join_room(f'campaign_{campaign_id}')
            logger.info('Joined campaign room', extra={'campaign_id': campaign_id, 'user_id': current_user.id})
        else:
            logger.warning('Unauthorized socket join attempt', extra={'user_id': current_user.id, 'campaign_id': campaign_id})
            return disconnect()

    @socketio.on('join_conversations')
    def on_join_conversations():
        if current_user.is_authenticated:
            join_room(f'user_{current_user.id}')
