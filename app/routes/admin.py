from flask import (Blueprint, render_template, request, redirect, url_for, flash, jsonify, session)
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from datetime import datetime
import pandas as pd

from app.extensions import db, limiter
from app.models import (User, Proxy, TelegramAccount, Campaign, Recipient, VerificationCode)
from app.services.proxy_service import assign_proxy_to_account
from app.services.auth_service import validate_strong_password
from app.services.campaign_service import get_campaign_stats
from app.services.telegram_service import TelegramService

admin_bp = Blueprint('admin', __name__)

@admin_bp.before_request
@login_required
def check_permissions():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    # Admins can access everything
    if current_user.role == 'admin':
        return
        
    endpoint = request.endpoint.split('.')[-1] if request.endpoint else ''
    
    proxy_endpoints = ['proxies', 'add_single_proxy', 'batch_add_proxies', 'toggle_proxy', 'delete_proxy']
    account_endpoints = ['accounts', 'add_single_account', 'batch_add_accounts', 'verify_account', 'toggle_account', 'delete_account']
    
    if endpoint in proxy_endpoints and current_user.can_add_proxies:
        return
        
    if endpoint in account_endpoints and current_user.can_add_numbers:
        return
        
    flash('Access denied. Insufficient permissions.', 'danger')
    return redirect(url_for('employee.dashboard'))

@admin_bp.route('/dashboard')
def dashboard():
    campaigns = db.session.query(Campaign).all()
    accounts = db.session.query(TelegramAccount).all()
    proxies = db.session.query(Proxy).all()
    employees = db.session.query(User).filter_by(role='employee').all()
    return render_template('admin/dashboard.html', campaigns=campaigns, accounts=accounts, proxies=proxies, employees=employees)

# ──────────── Proxies ────────────
@admin_bp.route('/proxies', methods=['GET'])
def proxies():
    proxies = db.session.query(Proxy).all()
    return render_template('admin/proxies.html', proxies=proxies)

@admin_bp.route('/proxies/add', methods=['POST'])
def add_single_proxy():
    ptype = request.form.get('type', 'socks5')
    host = request.form.get('host')
    port = request.form.get('port')
    username = request.form.get('username')
    password = request.form.get('password')
    
    if host and port:
        proxy = Proxy(proxy_type=ptype, host=host, port=int(port), username=username, password=password)
        db.session.add(proxy)
        db.session.commit()
        flash('Proxy added successfully!', 'success')
    else:
        flash('Host and Port are required.', 'danger')
    return redirect(url_for('admin.proxies'))

@admin_bp.route('/proxies/batch', methods=['POST'])
def batch_add_proxies():
    file = request.files.get('file')
    if not file:
        flash('No file uploaded', 'danger')
        return redirect(url_for('admin.proxies'))
    try:
        df = pd.read_csv(file)
        added = 0
        for _, row in df.iterrows():
            p = Proxy(
                proxy_type=row.get('type', 'socks5'), 
                host=row['host'], 
                port=int(row['port']), 
                username=row.get('username'), 
                password=row.get('password')
            )
            db.session.add(p)
            added += 1
        db.session.commit()
        flash(f'{added} proxies added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('admin.proxies'))

@admin_bp.route('/proxies/<int:id>/toggle', methods=['POST'])
def toggle_proxy(id):
    proxy = db.session.get(Proxy, id)
    proxy.is_active = not proxy.is_active
    db.session.commit()
    return redirect(url_for('admin.proxies'))

@admin_bp.route('/proxies/<int:id>/delete', methods=['POST'])
def delete_proxy(id):
    proxy = db.session.get(Proxy, id)
    db.session.delete(proxy)
    db.session.commit()
    flash('Proxy deleted', 'info')
    return redirect(url_for('admin.proxies'))

# ──────────── Telegram Accounts ────────────
@admin_bp.route('/accounts', methods=['GET'])
def accounts():
    accounts = db.session.query(TelegramAccount).all()
    proxies = db.session.query(Proxy).filter_by(is_active=True).all()
    free_proxy_available = any(p.accounts.count() < 5 for p in proxies)
    return render_template('admin/accounts.html', accounts=accounts, free_proxy_available=free_proxy_available)

@admin_bp.route('/accounts/add', methods=['POST'])
def add_single_account():
    phone = request.form.get('phone')
    api_id = request.form.get('api_id')
    api_hash = request.form.get('api_hash')
    
    if phone and api_id and api_hash:
        if db.session.query(TelegramAccount).filter_by(phone=phone).first():
            flash('Phone number already exists.', 'warning')
        else:
            account = TelegramAccount(phone=phone, api_id=api_id, api_hash=api_hash)
            assigned_proxy = assign_proxy_to_account(account)
            if not assigned_proxy:
                flash('Free proxy not available. Cannot add account.', 'danger')
            else:
                db.session.add(account)
                db.session.commit()
                flash('Account added successfully!', 'success')
    else:
        flash('All fields are required.', 'danger')
    return redirect(url_for('admin.accounts'))

@admin_bp.route('/accounts/batch', methods=['POST'])
def batch_add_accounts():
    api_id = request.form.get('api_id')
    api_hash = request.form.get('api_hash')
    phones_text = request.form.get('phones')
    
    if not api_id or not api_hash or not phones_text:
        flash('API ID, API Hash, and Phone numbers are required.', 'danger')
        return redirect(url_for('admin.accounts'))
    
    phones = [p.strip() for p in phones_text.splitlines() if p.strip()]
    added = 0
    proxy_failed = 0
    
    for phone in phones:
        if db.session.query(TelegramAccount).filter_by(phone=phone).first():
            continue
        account = TelegramAccount(phone=phone, api_id=api_id, api_hash=api_hash)
        assigned_proxy = assign_proxy_to_account(account)
        if not assigned_proxy:
            proxy_failed += 1
            break
        db.session.add(account)
        added += 1
        
    try:
        db.session.commit()
        if proxy_failed > 0:
            flash(f'{added} accounts added. Free proxy not available for the remaining {proxy_failed} numbers.', 'warning')
        else:
            flash(f'{added} accounts added successfully and assigned to proxies!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
        
    return redirect(url_for('admin.accounts'))

@admin_bp.route('/accounts/<int:id>/verify', methods=['GET', 'POST'])
def verify_account(id):
    account = db.session.get(TelegramAccount, id)
    if not account:
        flash('Account not found', 'danger')
        return redirect(url_for('admin.accounts'))

    tg = TelegramService()

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        password = request.form.get('password', '').strip() or None

        vr = db.session.query(VerificationCode).filter_by(account_id=account.id, is_consumed=False).order_by(VerificationCode.created_at.desc()).first()
        if not vr:
            flash('No pending verification. Start again.', 'danger')
            return redirect(url_for('admin.accounts'))

        result = tg.verify_code(account, code, vr.phone_code_hash, password=password)

        if result['status'] == 'success':
            vr.is_consumed = True
            db.session.commit()
            flash('Account verified successfully!', 'success')
            return redirect(url_for('admin.accounts'))
        else:
            flash(result.get('message', 'Verification failed'), 'danger')
            return render_template('admin/verify_code.html', account=account)

    result = tg.send_code(account)
    if result['status'] != 'success':
        flash(f"Failed to send code: {result.get('message')}", 'danger')
        return redirect(url_for('admin.accounts'))

    vr = VerificationCode(account_id=account.id, phone_code_hash=result['phone_code_hash'], is_consumed=False)
    db.session.add(vr)
    db.session.commit()

    flash('Code sent to Telegram. Enter it below.', 'info')
    return render_template('admin/verify_code.html', account=account)

@admin_bp.route('/accounts/<int:id>/toggle', methods=['POST'])
def toggle_account(id):
    account = db.session.get(TelegramAccount, id)
    account.is_active = not account.is_active
    db.session.commit()
    return redirect(url_for('admin.accounts'))

@admin_bp.route('/accounts/<int:id>/delete', methods=['POST'])
def delete_account(id):
    account = db.session.get(TelegramAccount, id)
    if account:
        db.session.delete(account)
        db.session.commit()
        flash('Telegram account deleted successfully.', 'info')
    return redirect(url_for('admin.accounts'))

# ──────────── Employees ────────────
@admin_bp.route('/employees', methods=['GET', 'POST'])
def employees():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('employee.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        can_replies = 'can_handle_replies' in request.form

        if not username or not password:
            flash('Username and password required.', 'danger')
            return redirect(url_for('admin.employees'))

        ok, err = validate_strong_password(password)
        if not ok:
            flash(err, 'danger')
            return redirect(url_for('admin.employees'))

        if db.session.query(User).filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('admin.employees'))

        user = User(username=username, email=email, password_hash=generate_password_hash(password), role='employee', can_handle_replies=can_replies)
        db.session.add(user)
        db.session.commit()
        flash('Employee created successfully.', 'success')
        return redirect(url_for('admin.employees'))

    employees = db.session.query(User).filter_by(role='employee').all()
    return render_template('admin/employees.html', employees=employees)

@admin_bp.route('/employees/<int:id>/toggle', methods=['POST'])
def toggle_employee(id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('employee.dashboard'))
        
    user = db.session.get(User, id)
    user.is_active = not user.is_active
    db.session.commit()
    return redirect(url_for('admin.employees'))

# ──────────── Campaigns ────────────
@admin_bp.route('/campaigns', methods=['GET', 'POST'])
def campaigns():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('employee.dashboard'))

    if request.method == 'POST':
        name = request.form.get('name')
        message = request.form.get('message')
        employee_id = request.form.get('employee_id')
        delay = int(request.form.get('delay', 2))
        daily_limit = int(request.form.get('daily_limit', 50))
        account_ids = request.form.getlist('account_ids')

        if not name or not message:
            flash('Name and message required.', 'danger')
            return redirect(url_for('admin.campaigns'))

        campaign = Campaign(name=name, message=message, delay_seconds=delay, daily_limit=daily_limit, created_by=current_user.id, employee_id=employee_id or None)
        db.session.add(campaign)
        db.session.flush()

        for acc_id in account_ids:
            acc = db.session.get(TelegramAccount, int(acc_id))
            if acc:
                campaign.selected_accounts.append(acc)

        db.session.commit()
        flash(f'Campaign "{campaign.name}" created.', 'success')
        return redirect(url_for('admin.campaign_detail', id=campaign.id))

    campaigns = db.session.query(Campaign).order_by(Campaign.created_at.desc()).all()
    employees = db.session.query(User).filter_by(role='employee', is_active=True, can_handle_replies=True).all()
    accounts = db.session.query(TelegramAccount).filter_by(is_active=True, is_verified=True).all()
    return render_template('admin/campaigns.html', campaigns=campaigns, employees=employees, accounts=accounts)

@admin_bp.route('/campaign/<int:id>', methods=['GET', 'POST'])
def campaign_detail(id):
    campaign = db.session.get(Campaign, id)
    if not campaign:
        flash('Campaign not found', 'danger')
        return redirect(url_for('admin.campaigns'))

    if request.method == 'POST':
        usernames_text = request.form.get('usernames')
        if not usernames_text:
            flash('No usernames provided.', 'danger')
            return redirect(url_for('admin.campaign_detail', id=id))
        
        usernames = [u.strip().lstrip('@') for u in usernames_text.splitlines() if u.strip()]
        added = 0
        
        for username in usernames:
            if not db.session.query(Recipient).filter_by(campaign_id=id, username=username).first():
                rec = Recipient(campaign_id=id, username=username)
                db.session.add(rec)
                added += 1
                
        try:
            db.session.commit()
            flash(f'{added} recipients uploaded successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
            
        return redirect(url_for('admin.campaign_detail', id=id))

    recipients = campaign.recipients.limit(500).all()
    stats = get_campaign_stats(id)
    return render_template('admin/campaign_detail.html', campaign=campaign, recipients=recipients, stats=stats)

@admin_bp.route('/campaign/<int:id>/start', methods=['POST'])
def start_campaign(id):
    campaign = db.session.get(Campaign, id)
    if not campaign:
        flash('Campaign not found', 'danger')
        return redirect(url_for('admin.campaigns'))

    if campaign.status == 'running':
        flash('Campaign is already running.', 'warning')
        return redirect(url_for('admin.campaign_detail', id=id))

    if not campaign.selected_accounts:
        flash('Cannot start campaign. No Telegram accounts selected.', 'danger')
        return redirect(url_for('admin.campaign_detail', id=id))

    campaign.status = 'running'
    campaign.started_at = datetime.utcnow()
    db.session.commit()

    flash('Campaign started. Background sender will process it shortly.', 'success')
    return redirect(url_for('admin.campaign_detail', id=id))

@admin_bp.route('/campaign/<int:id>/pause', methods=['POST'])
def pause_campaign(id):
    campaign = db.session.get(Campaign, id)
    if campaign.status == 'running':
        campaign.status = 'paused'
        db.session.commit()
        flash('Campaign paused.', 'info')
    else:
        flash('Campaign is not running.', 'warning')
    return redirect(url_for('admin.campaign_detail', id=id))

@admin_bp.route('/campaign/<int:id>/delete', methods=['POST'])
def delete_campaign(id):
    campaign = db.session.get(Campaign, id)
    db.session.delete(campaign)
    db.session.commit()
    flash('Campaign deleted.', 'info')
    return redirect(url_for('admin.campaigns'))

# ──────────── Dead Letter Queue ────────────
@admin_bp.route('/dead-letter')
def dead_letter_queue():
    items = db.session.query(Recipient).filter_by(status='dead_letter').all()
    return render_template('admin/dead_letter.html', items=items)

@admin_bp.route('/dead-letter/<int:rid>/retry', methods=['POST'])
def retry_dead_letter(rid):
    rec = db.session.get(Recipient, rid)
    rec.status = 'pending'
    rec.retry_count = 0
    rec.last_error = None
    rec.dead_letter_reason = None
    rec.dead_lettered_at = None
    db.session.commit()
    flash('Recipient moved back to pending.', 'info')
    return redirect(url_for('admin.dead_letter_queue'))

# ──────────── Health & System Monitoring ────────────
@admin_bp.route('/health')
def health_dashboard():
    from app.models import WorkerHeartbeat
    workers = db.session.query(WorkerHeartbeat).all()
    proxies = db.session.query(Proxy).all()
    accounts = db.session.query(TelegramAccount).all()
    
    proxy_stats = {
        'healthy': sum(1 for p in proxies if p.health_status == 'healthy'),
        'unhealthy': sum(1 for p in proxies if p.health_status == 'unhealthy'),
        'testing': sum(1 for p in proxies if p.health_status == 'testing'),
        'disabled': sum(1 for p in proxies if not p.is_active),
    }
    
    account_stats = {
        'healthy': sum(1 for a in accounts if a.health_status == 'healthy'),
        'disconnected': sum(1 for a in accounts if a.health_status == 'disconnected'),
        'unauthorized': sum(1 for a in accounts if a.health_status == 'unauthorized'),
        'disabled': sum(1 for a in accounts if not a.is_active),
    }

    return render_template('admin/health.html', workers=workers, proxy_stats=proxy_stats, account_stats=account_stats)

@admin_bp.route('/proxy/<int:id>/test', methods=['POST'])
def test_proxy(id):
    from app.services.telegram_service import TelegramService
    proxy = db.session.get(Proxy, id)
    proxy.health_status = 'testing'
    db.session.commit()

    tg = TelegramService()
    ok, latency, err = tg.test_proxy_connection(proxy)

    proxy.last_checked_at = datetime.utcnow()
    if ok:
        proxy.health_status = 'healthy'
        proxy.latency_ms = latency
        proxy.last_success_at = datetime.utcnow()
        proxy.success_count += 1
        proxy.last_error = None
    else:
        proxy.health_status = 'unhealthy'
        proxy.last_failure_at = datetime.utcnow()
        proxy.failure_count += 1
        proxy.last_error = err

    db.session.commit()
    return jsonify({'status': 'healthy' if ok else 'unhealthy', 'latency': latency, 'error': err})

@admin_bp.route('/account/<int:id>/test', methods=['POST'])
def test_account(id):
    from app.services.telegram_service import TelegramService
    account = db.session.get(TelegramAccount, id)
    
    tg = TelegramService()
    ok, msg = tg.test_account_health(account)

    account.last_health_check = datetime.utcnow()
    account.health_status = 'healthy' if ok else 'disconnected'
    account.last_error = None if ok else msg
    if ok:
        account.last_successful_connection = datetime.utcnow()
        
    db.session.commit()
    return jsonify({'status': 'healthy' if ok else 'unhealthy', 'message': msg})
