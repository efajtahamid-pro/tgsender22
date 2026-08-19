from app.models import Proxy, TelegramAccount
from app.extensions import db

def assign_proxy_to_account(account):
    proxies = db.session.query(Proxy).filter_by(is_active=True).all()
    available = [p for p in proxies if p.accounts.count() < 5]
    if available:
        best = min(available, key=lambda p: p.accounts.count())
        account.proxy_id = best.id
        return True, best, "Assigned to proxy."

    unproxied_count = db.session.query(TelegramAccount).filter(
        TelegramAccount.proxy_id.is_(None), TelegramAccount.is_active == True
    ).count()
    
    if unproxied_count < 3:
        account.proxy_id = None
        return True, None, "No proxy slots available. Added without proxy."
        
    return False, None, "Free proxy not available and unproxied limit (3) reached."
