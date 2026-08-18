# Append to app/routes/admin.py

@admin_bp.route('/health')
def health_dashboard():
    from app.models import WorkerHeartbeat, Proxy, TelegramAccount
    
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
