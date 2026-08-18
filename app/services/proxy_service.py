from app.models import Proxy, TelegramAccount
from app.extensions import db

def assign_proxy_to_account(account):
    proxies = db.session.query(Proxy).filter_by(is_active=True).all()
    if not proxies:
        return None

    available = [p for p in proxies if p.accounts.count() < 5]
    if available:
        best = min(available, key=lambda p: p.accounts.count())
    else:
        best = min(proxies, key=lambda p: p.accounts.count())
        
    account.proxy_id = best.id
    return best