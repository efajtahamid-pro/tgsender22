from app.models import Proxy, TelegramAccount
from app.extensions import db
from sqlalchemy import func
from flask import current_app

def assign_proxy_to_account(account):
    """
    Assigns the least-loaded active proxy to a new account.
    Proxies with status 'healthy' or 'unknown' are eligible.
    """
    max_retries = 3
    max_capacity = current_app.config.get('MAX_ACCOUNTS_PER_PROXY', 5)

    for _ in range(max_retries):
        # 1. Efficient aggregate query to find the best proxy (No N+1 queries)
        proxy_id = db.session.query(
            Proxy.id
        ).outerjoin(
            TelegramAccount, Proxy.id == TelegramAccount.proxy_id
        ).filter(
            Proxy.is_active.is_(True),
            # FIX: Allow 'unknown' so new proxies can be used immediately
            Proxy.health_status.in_(['healthy', 'unknown'])
        ).group_by(
            Proxy.id
        ).having(
            func.count(TelegramAccount.id) < max_capacity
        ).order_by(
            func.count(TelegramAccount.id).asc()
        ).limit(1).scalar()
        
        if not proxy_id:
            return False, None, "No active proxies with available capacity. Add more proxies."

        # 2. Lock the proxy row (Requires PostgreSQL for true row-level locking)
        proxy = db.session.query(Proxy).filter_by(id=proxy_id).with_for_update().first()
        
        # 3. Re-verify capacity inside the lock
        current_count = db.session.query(TelegramAccount).filter_by(proxy_id=proxy.id).count()
        if current_count < max_capacity:
            account.proxy_id = proxy.id
            return True, proxy, "Assigned to proxy."
            
    return False, None, "Failed to assign proxy after multiple retries due to high contention."
