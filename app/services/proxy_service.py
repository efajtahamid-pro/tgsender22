from app.models import Proxy, TelegramAccount
from app.extensions import db

def assign_proxy_to_account(account):
    """
    Assigns the least-used active proxy (max 5 accounts per proxy).
    If all proxies are full or none exist, allows up to 3 accounts to be unproxied.
    Returns: (success: bool, proxy_obj: Proxy or None, message: str)
    """
    proxies = db.session.query(Proxy).filter_by(is_active=True).all()
    
    # 1. Try to find an available proxy with < 5 accounts
    available = [p for p in proxies if p.accounts.count() < 5]
    if available:
        best = min(available, key=lambda p: p.accounts.count())
        account.proxy_id = best.id
        return True, best, "Assigned to proxy."

    # 2. If no proxy slots, check unproxied limit (Max 3)
    unproxied_count = db.session.query(TelegramAccount).filter(
        TelegramAccount.proxy_id.is_(None), 
        TelegramAccount.is_active == True
    ).count()
    
    if unproxied_count < 3:
        account.proxy_id = None
        return True, None, "No proxy slots available. Added without proxy."
        
    return False, None, "Free proxy not available and unproxied limit (3) reached."
