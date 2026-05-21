import os
import firebase_helper as fb

# ─── Firebase (still needed to boot before anything else) ───────────────────
FIREBASE_URL    = os.environ.get("FIREBASE_URL", "")
FIREBASE_SECRET = os.environ.get("FIREBASE_SECRET", "")

# ─── Flask ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "hamzashop-secret-2026")
FLASK_PORT  = int(os.environ.get("PORT", 5000))

# ─── Runtime helpers — read live from Firebase ───────────────────────────────
def get_config():
    return fb.get("config") or {}

def cfg(key, default=""):
    return get_config().get(key, default)

# Shortcuts used across bot / app
def BOT_TOKEN():      return cfg("bot_token")
def BOT_USERNAME():   return cfg("bot_username", "YourBotUsername")
def SUPPORT_USERNAME():return cfg("support_username", "@support")
def PAYMENT_LINK():   return cfg("payment_link", "")
def RULES_TEXT():     return cfg("rules_text", "📋 Rules coming soon.")
def ADMIN_USERNAME(): return cfg("admin_username", "hamza")
def ADMIN_PASSWORD(): return cfg("admin_password", "hamza")
def PANEL_NAME():     return cfg("panel_name", "HamzaShop Admin")
def PANEL_COPYRIGHT():return cfg("panel_copyright", "© 2026 HamzaShop")
def MIN_DEPOSIT():    return int(cfg("min_deposit", 1000))
def MIN_WITHDRAWAL(): return int(cfg("min_withdrawal", 100))
def MAX_WITHDRAWAL(): return int(cfg("max_withdrawal", 50000))
def REFER_COMMISSION():return float(cfg("refer_commission", 10))
def NOTIFY_CHAT_IDS():
    raw = cfg("notify_chat_ids","")
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else []
