from datetime import datetime
from flask_login import UserMixin
from app.extensions import db

# Association table for Campaign and TelegramAccount (Many-to-Many)
campaign_accounts = db.Table(
    'campaign_accounts',
    db.Column('campaign_id', db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'), primary_key=True),
    db.Column('account_id', db.Integer, db.ForeignKey('telegram_accounts.id', ondelete='CASCADE'), primary_key=True)
)

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='employee', nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True)
    can_add_proxies = db.Column(db.Boolean, default=False)
    can_add_numbers = db.Column(db.Boolean, default=False)
    can_handle_replies = db.Column(db.Boolean, default=False)

    totp_secret = db.Column(db.String(64), nullable=True)
    is_2fa_enabled = db.Column(db.Boolean, default=False)

    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    campaigns = db.relationship('Campaign', backref='creator', lazy='dynamic', foreign_keys='Campaign.created_by')
    conversations = db.relationship('Conversation', backref='employee', lazy='dynamic', foreign_keys='Conversation.employee_id')

class Proxy(db.Model):
    __tablename__ = 'proxies'
    id = db.Column(db.Integer, primary_key=True)
    proxy_type = db.Column(db.String(10), default='socks5')
    host = db.Column(db.String(200), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(100))
    password = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accounts = db.relationship('TelegramAccount', backref='proxy', lazy='dynamic', cascade='all, delete-orphan')

class TelegramAccount(db.Model):
    __tablename__ = 'telegram_accounts'
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    api_id = db.Column(db.String(20), nullable=False)
    api_hash = db.Column(db.String(100), nullable=False)
    session_string = db.Column(db.Text)
    proxy_id = db.Column(db.Integer, db.ForeignKey('proxies.id'), nullable=True, index=True)

    is_verified = db.Column(db.Boolean, default=False, index=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    is_healthy = db.Column(db.Boolean, default=True)
    is_2fa = db.Column(db.Boolean, default=False)

    daily_sent = db.Column(db.Integer, default=0)
    daily_limit = db.Column(db.Integer, default=50)
    total_sent = db.Column(db.Integer, default=0)
    last_used = db.Column(db.DateTime)
    last_reset = db.Column(db.DateTime, default=datetime.utcnow)

    recipients = db.relationship('Recipient', backref='account', lazy='dynamic', foreign_keys='Recipient.assigned_account_id')

class Campaign(db.Model):
    __tablename__ = 'campaigns'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False) # Store multiple lines here
    status = db.Column(db.String(20), default='draft', nullable=False, index=True)
    delay_seconds = db.Column(db.Integer, default=2)
    daily_limit = db.Column(db.Integer, default=50)

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    # Many-to-Many relationship for selected accounts
    selected_accounts = db.relationship('TelegramAccount', secondary=campaign_accounts, backref='campaigns_used')

    recipients = db.relationship('Recipient', backref='campaign', lazy='dynamic', cascade='all, delete-orphan')
    conversations = db.relationship('Conversation', backref='campaign', lazy='dynamic', cascade='all, delete-orphan')
    
    employee = db.relationship('User', foreign_keys=[employee_id], backref='assigned_campaigns')

class Recipient(db.Model):
    __tablename__ = 'recipients'
    __table_args__ = (
        db.UniqueConstraint('campaign_id', 'username', name='uq_campaign_username'),
        db.Index('ix_recipient_campaign_status', 'campaign_id', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'), nullable=False, index=True)
    
    username = db.Column(db.String(100), nullable=False, index=True)
    user_id = db.Column(db.BigInteger, nullable=True, index=True) 

    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    assigned_account_id = db.Column(db.Integer, db.ForeignKey('telegram_accounts.id'), nullable=True, index=True)

    sent_at = db.Column(db.DateTime)
    replied_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    retry_count = db.Column(db.Integer, default=0)

    dead_letter_reason = db.Column(db.Text, nullable=True)
    dead_lettered_at = db.Column(db.DateTime, nullable=True)

    conversation = db.relationship('Conversation', backref='recipient', uselist=False, cascade='all, delete-orphan')

class Conversation(db.Model):
    __tablename__ = 'conversations'
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('recipients.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    is_active = db.Column(db.Boolean, default=True)
    unread_count = db.Column(db.Integer, default=0)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    messages = db.relationship('Message', backref='conversation', lazy='dynamic', order_by='Message.timestamp', cascade='all, delete-orphan')

class Message(db.Model):
    __tablename__ = 'messages'
    __table_args__ = (db.Index('ix_message_conv_timestamp', 'conversation_id', 'timestamp'),)
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    sender = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    telegram_message_id = db.Column(db.BigInteger, nullable=True, index=True)

class VerificationCode(db.Model):
    __tablename__ = 'verification_codes'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('telegram_accounts.id'), nullable=False, index=True)
    phone_code_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_consumed = db.Column(db.Boolean, default=False)