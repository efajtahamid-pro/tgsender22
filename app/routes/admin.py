from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, current_app)
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from datetime import datetime
import pandas as pd
from sqlalchemy import func

from app.extensions import db
from app.models import (User, Proxy, TelegramAccount, Campaign, Recipient,
                        VerificationCode, SendLog, ReplyCheckpoint)
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
    if current_user.role == 'admin':
        return
    endpoint = request.endpoint.split('.')[-1] if request.endpoint else ''
    if endpoint in ['proxies', 'add_single_proxy', 'batch_add_proxies',
                    'toggle_proxy', 'delete_proxy'] and current_user.can_add_proxies:
        return
    if endpoint in ['accounts', 'add_single_account', 'batch_add_accounts',
                    'verify_account', 'toggle_account', 'delete_account'] and current_user.can_add_numbers:
        return
    flash('Access denied.', 'danger')
    return redirect(url_for('employee.dashboard'))

@admin_bp.route('/dashboard')
def dashboard():
    return render_template(
        'admin/dashboard.html',
        campaigns=db.session.query(Campaign).all(),
        accounts=db.session.query(TelegramAccount).all(),
        proxies=db.session.query(Proxy).all(),
        employees=db.session.query(User).filter_by(role='employee').all()
    )

# ─── Proxies ───

@admin_bp.route('/proxies', methods=['GET'])
def proxies():
    return render_template('admin/proxies.html', proxies=db.session.query(Proxy).all())

@admin_bp.route('/proxies/add', methods=['POST'])
def add_single_proxy():
    ptype = request.form.get('type', 'socks5')
    host = request.form.get('host')
    port = request.form.get('port')
    if host and port:
        db.session.add(Proxy(
            proxy_type=ptype, host=host, port=int(port),
            username=request.form.get('username'),
            password=request.form.get('password')
        ))
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
        required_cols = {'host', 'port'}
        if not required_cols.issubset(set(df.columns)):
            missing = required_cols - set(df.columns)
            flash(f'CSV missing required columns: {missing}. Required: host, port', 'danger')
            return redirect(url_for('admin.proxies'))

        added = 0
        errors = []
        for idx, row in df.iterrows():
            try:
                host = str(row['host']).strip()
                port = int(row['port'])
                if not host:
                    raise ValueError('Empty host')
                db.session.add(Proxy(
                    proxy_type=str(row.get('type', 'socks5')).strip() if pd.notna(row.get('type')) else 'socks5',
                    host=host,
                    port=port,
                    username=str(row['username']).strip() if pd.notna(row.get('username')) else None,
                    password=str(row['password']).strip() if pd.notna(row.get('password')) else None,
                ))
                added += 1
            except Exception as row_err:
                errors.append(f"Row {idx + 2}: {row_err}")
        db.session.commit()
        if errors:
            flash(f'Added {added} proxies. {len(errors)} rows failed: {"; ".join(errors[:5])}', 'warning')
        else:
            flash(f'{added} proxies added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'CSV parse error: {e}', 'danger')
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
    if not proxy:
        flash('Proxy not found.', 'danger')
        return redirect(url_for('admin.proxies'))

    account_count = proxy.accounts.count()
    if account_count > 0:
        flash(
            f'Cannot delete proxy "{proxy.host}:{proxy.port}" — '
            f'{account_count} account(s) are still attached. '
            f'Reassign or delete those accounts first.',
            'danger'
        )
        return redirect(url_for('admin.proxies'))

    db.session.delete(proxy)
    db.session.commit()
    flash('Proxy deleted.', 'info')
    return redirect(url_for('admin.proxies'))

# ─── Telegram Accounts ───

@admin_bp.route('/accounts', methods=['GET'])
def accounts():
    max_capacity = current_app.config.get('MAX_ACCOUNTS_PER_PROXY', 5)
    
    # FIX: Include 'unknown' status so new proxies can be used immediately before testing
    capacity_count = db.session.query(
        func.count(Proxy.id)
    ).outerjoin(
        TelegramAccount, Proxy.id == TelegramAccount.proxy_id
    ).filter(
        Proxy.is_active.is_(True),
        Proxy.health_status.in_(['healthy', 'unknown'])
    ).group_by(
        Proxy.id
    ).having(
        func.count(TelegramAccount.id) < max_capacity
    ).count()
    
    return render_template(
        'admin/accounts.html',
        accounts=db.session.query(TelegramAccount).all(),
        proxy_capacity_available=capacity_count > 0
    )

@admin_bp.route('/accounts/add', methods=['POST'])
def add_single_account():
    phone = request.form.get('phone')
    if phone:
        if db.session.query(TelegramAccount).filter_by(phone=phone).first():
            flash('Phone number already exists.', 'warning')
        else:
            acc = TelegramAccount(phone=phone, api_id="global", api_hash="global")
            ok, _, msg = assign_proxy_to_account(acc)
            if not ok:
                flash(msg, 'danger')
            else:
                db.session.add(acc)
                db.session.commit()
                flash(f'Account added! {msg}', 'success')
    else:
        flash('Phone number is required.', 'danger')
    return redirect(url_for('admin.accounts'))

@admin_bp.route('/accounts/batch', methods=['POST'])
def batch_add_accounts():
    phones_text = request.form.get('phones')
    if not phones_text:
        flash('Missing data', 'danger')
        return redirect(url_for('admin.accounts'))

    added = 0
    skipped = 0
    failed = 0
    for phone in [p.strip() for p in phones_text.splitlines() if p.strip()]:
        if db.session.query(TelegramAccount).filter_by(phone=phone).first():
            skipped += 1
            continue
        acc = TelegramAccount(phone=phone, api_id="global", api_hash="global")
        ok, _, msg = assign_proxy_to_account(acc)
        if not ok:
            flash(f'Stopped at {phone}: {msg}', 'danger')
            failed += 1
            break
        db.session.add(acc)
        added += 1
    db.session.commit()
    if skipped or failed:
        flash(f'{added} accounts added. {skipped} skipped. {failed} failed.', 'warning')
    else:
        flash(f'{added} accounts added.', 'success')
    return redirect(url_for('admin.accounts'))

@admin_bp.route('/accounts/<int:id>/verify', methods=['GET', 'POST'])
def verify_account(id):
    account = db.session.get(TelegramAccount, id)
    tg = TelegramService()
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        password = request.form.get('password', '').strip() or None
        vr = db.session.query(VerificationCode).filter_by(
            account_id=account.id, is_consumed=False
        ).order_by(VerificationCode.created_at.desc()).first()
        if not vr:
            flash('No pending verification. Please request a new code.', 'danger')
            return redirect(url_for('admin.accounts'))
        res = tg.verify_code(account, code, vr.phone_code_hash, password=password)
        if res['status'] == 'success':
            vr.is_consumed = True
            db.session.commit()
            flash('Account verified!', 'success')
            return redirect(url_for('admin.accounts'))
        flash(res.get('message', 'Failed'), 'danger')
        return render_template('admin/verify_code.html', account=account)

    res = tg.send_code(account)
    if res['status'] != 'success':
        flash(f"Failed: {res.get('message')}", 'danger')
        return redirect(url_for('admin.accounts'))
    db.session.add(VerificationCode(account_id=account.id, phone_code_hash=res['phone_code_hash']))
    db.session.commit()
    flash('Code sent to Telegram.', 'info')
    return render_template('admin/verify_code.html', account=account)

@admin_bp.route('/accounts/<int:id>/toggle', methods=['POST'])
def toggle_account(id):
    acc = db.session.get(TelegramAccount, id)
    acc.is_active = not acc.is_active
    db.session.commit()
    return redirect(url_for('admin.accounts'))

@admin_bp.route('/accounts/<int:id>/delete', methods=['POST'])
def delete_account(id):
    acc = db.session.get(TelegramAccount, id)
    if acc:
        try:
            db.session.query(Recipient).filter_by(assigned_account_id=id).update({'assigned_account_id': None})
            db.session.query(SendLog).filter_by(account_id=id).delete(synchronize_session=False)
            db.session.query(VerificationCode).filter_by(account_id=id).delete(synchronize_session=False)
            db.session.query(ReplyCheckpoint).filter_by(account_id=id).delete(synchronize_session=False)
            
            db.session.delete(acc)
            db.session.commit()
            flash('Account deleted successfully.', 'info')
        except Exception as e:
            db.session.rollback()
            flash(f'Error deleting account: {e}', 'danger')
    return redirect(url_for('admin.accounts'))

# ─── Employees ───

@admin_bp.route('/employees', methods=['GET', 'POST'])
def employees():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('employee.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            flash('Username and password required.', 'danger')
            return redirect(url_for('admin.employees'))
        ok, err = validate_strong_password(password)
        if not ok:
            flash(err, 'danger')
            return redirect(url_for('admin.employees'))
        if db.session.query(User).filter_by(username=username).first():
            flash('Username exists.', 'danger')
            return redirect(url_for('admin.employees'))
        db.session.add(User(
            username=username,
            email=request.form.get('email'),
            password_hash=generate_password_hash(password),
            role='employee',
            can_handle_replies='can_handle_replies' in request.form
        ))
        db.session.commit()
        flash('Employee created.', 'success')
        return redirect(url_for('admin.employees'))
    return render_template('admin/employees.html',
                          employees=db.session.query(User).filter_by(role='employee').all())

@admin_bp.route('/employees/<int:id>/toggle', methods=['POST'])
def toggle_employee(id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('employee.dashboard'))
    user = db.session.get(User, id)
    user.is_active = not user.is_active
    db.session.commit()
    return redirect(url_for('admin.employees'))

# ─── Campaigns ───

@admin_bp.route('/campaigns', methods=['GET', 'POST'])
def campaigns():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('employee.dashboard'))
    if request.method == 'POST':
        name = request.form.get('name')
        message = request.form.get('message')
        if not name or not message:
            flash('Name and message required.', 'danger')
            return redirect(url_for('admin.campaigns'))
        camp = Campaign(
            name=name, message=message,
            delay_seconds=int(request.form.get('delay', 2)),
            daily_limit=int(request.form.get('daily_limit', 50)),
            created_by=current_user.id,
            employee_id=request.form.get('employee_id') or None
        )
        db.session.add(camp)
        db.session.flush()
        for acc_id in request.form.getlist('account_ids'):
            acc = db.session.get(TelegramAccount, int(acc_id))
            if acc:
                camp.selected_accounts.append(acc)
        db.session.commit()
        flash('Campaign created.', 'success')
        return redirect(url_for('admin.campaign_detail', id=camp.id))
    return render_template(
        'admin/campaigns.html',
        campaigns=db.session.query(Campaign).order_by(Campaign.created_at.desc()).all(),
        employees=db.session.query(User).filter_by(role='employee', is_active=True, can_handle_replies=True).all(),
        accounts=db.session.query(TelegramAccount).filter_by(is_active=True, is_verified=True).all()
    )

@admin_bp.route('/campaign/<int:id>', methods=['GET', 'POST'])
def campaign_detail(id):
    camp = db.session.get(Campaign, id)
    if not camp:
        flash('Not found', 'danger')
        return redirect(url_for('admin.campaigns'))

    if request.method == 'POST':
        usernames_text = request.form.get('usernames')
        if not usernames_text:
            flash('No usernames', 'danger')
            return redirect(url_for('admin.campaign_detail', id=id))
        added = 0
        skipped = 0
        for u in [u.strip().lstrip('@') for u in usernames_text.splitlines() if u.strip()]:
            u_lower = u.lower()
            if not u_lower:
                continue
            if db.session.query(Recipient).filter_by(campaign_id=id, username=u_lower).first():
                skipped += 1
                continue
            db.session.add(Recipient(campaign_id=id, username=u_lower))
            added += 1
        db.session.commit()
        if skipped:
            flash(f'{added} recipients uploaded. {skipped} duplicates skipped.', 'success')
        else:
            flash(f'{added} recipients uploaded.', 'success')
        return redirect(url_for('admin.campaign_detail', id=id))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 100, type=int)
    recipients_pag = db.session.query(Recipient).filter_by(campaign_id=id) \
        .order_by(Recipient.id).paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        'admin/campaign_detail.html',
        campaign=camp,
        recipients=recipients_pag.items,
        pagination=recipients_pag,
        stats=get_campaign_stats(id)
    )

@admin_bp.route('/campaign/<int:id>/start', methods=['POST'])
def start_campaign(id):
    camp = db.session.get(Campaign, id)
    if not camp:
        flash('Not found', 'danger')
        return redirect(url_for('admin.campaigns'))
    if camp.status == 'running':
        flash('Already running.', 'warning')
        return redirect(url_for('admin.campaign_detail', id=id))
    if not camp.selected_accounts:
        flash('No accounts selected.', 'danger')
        return redirect(url_for('admin.campaign_detail', id=id))
    if not camp.recipients.count():
        flash('No recipients added.', 'danger')
        return redirect(url_for('admin.campaign_detail', id=id))
    camp.status = 'running'
    camp.started_at = datetime.utcnow()
    db.session.commit()
    flash('Campaign started.', 'success')
    return redirect(url_for('admin.campaign_detail', id=id))

@admin_bp.route('/campaign/<int:id>/pause', methods=['POST'])
def pause_campaign(id):
    camp = db.session.get(Campaign, id)
    if camp.status == 'running':
        camp.status = 'paused'
        db.session.commit()
        flash('Paused.', 'info')
    return redirect(url_for('admin.campaign_detail', id=id))

@admin_bp.route('/campaign/<int:id>/delete', methods=['POST'])
def delete_campaign(id):
    camp = db.session.get(Campaign, id)
    if not camp:
        flash('Not found', 'danger')
        return redirect(url_for('admin.campaigns'))
    db.session.query(SendLog).filter(SendLog.campaign_id == id).delete(synchronize_session=False)
    db.session.delete(camp)
    db.session.commit()
    flash('Campaign deleted.', 'info')
    return redirect(url_for('admin.campaigns'))

# ─── Dead Letter Queue ───

@admin_bp.route('/dead-letter')
def dead_letter_queue():
    return render_template(
        'admin/dead_letter.html',
        items=db.session.query(Recipient).filter_by(status='dead_letter').all()
    )

@admin_bp.route('/dead-letter/<int:rid>/retry', methods=['POST'])
def retry_dead_letter(rid):
    rec = db.session.get(Recipient, rid)
    rec.status = 'pending'
    rec.retry_count = 0
    rec.last_error = None
    rec.dead_letter_reason = None
    rec.dead_lettered_at = None
    db.session.commit()
    flash('Moved back to pending.', 'info')
    return redirect(url_for('admin.dead_letter_queue'))

# ─── Health ───

@admin_bp.route('/health')
def health_dashboard():
    from app.models import WorkerHeartbeat
    workers = db.session.query(WorkerHeartbeat).all()
    proxies = db.session.query(Proxy).all()
    accounts = db.session.query(TelegramAccount).all()

    now = datetime.utcnow()
    stale_workers = []
    for w in workers:
        if w.last_heartbeat and (now - w.last_heartbeat).total_seconds() > 60:
            stale_workers.append(w.worker_name)

    if stale_workers:
        flash(f'Warning: Worker(s) {", ".join(stale_workers)} have not sent a heartbeat in >60s. '
              f'Is `python worker.py` running?', 'warning')

    return render_template(
        'admin/health.html',
        workers=workers,
        proxy_stats={
            'healthy': sum(1 for p in proxies if p.health_status == 'healthy'),
            'unhealthy': sum(1 for p in proxies if p.health_status == 'unhealthy'),
        },
        account_stats={
            'healthy': sum(1 for a in accounts if a.health_status == 'healthy'),
            'disconnected': sum(1 for a in accounts if a.health_status == 'disconnected'),
        }
    )

# ─── Test Endpoints ───

@admin_bp.route('/proxy/<int:id>/test', methods=['POST'])
def test_proxy(id):
    proxy = db.session.get(Proxy, id)
    # FIX: Prevent 500 crash if user clicks test on a proxy that was just deleted
    if not proxy:
        return jsonify({'status': 'unhealthy', 'latency': 0, 'error': 'Proxy not found in database'}), 404
        
    proxy.health_status = 'testing'
    db.session.commit()
    
    ok, latency, err = TelegramService().test_proxy_connection(proxy)
    proxy.last_checked_at = datetime.utcnow()
    if ok:
        proxy.health_status = 'healthy'
        proxy.latency_ms = latency
        proxy.last_success_at = datetime.utcnow()
        proxy.last_error = None
        proxy.success_count = (proxy.success_count or 0) + 1
    else:
        proxy.health_status = 'unhealthy'
        proxy.last_failure_at = datetime.utcnow()
        proxy.last_error = err
        proxy.failure_count = (proxy.failure_count or 0) + 1
    db.session.commit()
    return jsonify({'status': 'healthy' if ok else 'unhealthy', 'latency': latency, 'error': err})

@admin_bp.route('/account/<int:id>/test', methods=['POST'])
def test_account(id):
    acc = db.session.get(TelegramAccount, id)
    if not acc:
        return jsonify({'status': 'unhealthy', 'message': 'Account not found'}), 404
        
    ok, msg = TelegramService().test_account_health(acc)
    acc.last_health_check = datetime.utcnow()
    acc.health_status = 'healthy' if ok else 'disconnected'
    acc.last_error = None if ok else msg
    if ok:
        acc.last_successful_connection = datetime.utcnow()
    db.session.commit()
    return jsonify({'status': 'healthy' if ok else 'unhealthy', 'message': msg})
