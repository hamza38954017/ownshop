import os

# ─── Telegram Bot ───────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "YourBotUsername")
CHANNEL_LINK     = "https://t.me/shopatcheap"
CHANNEL_ID       = "@shopatcheap"
SUPPORT_USERNAME = "@Hamza3895"
PAYMENT_LINK     = os.environ.get("PAYMENT_LINK", "https://your-payment-link.com")

# ─── Firebase Realtime Database — REST only (/.json API) ─────────────────────
# No service account, no SDK, no API key needed.
# Firebase Console → Realtime Database → Rules → set read/write: true
# OR add a DB secret: Project Settings → Service Accounts → Database Secrets
FIREBASE_URL    = os.environ.get("FIREBASE_URL",
                  "https://your-project-default-rtdb.firebaseio.com")
# Leave FIREBASE_SECRET blank if your DB rules allow public read/write
FIREBASE_SECRET = os.environ.get("FIREBASE_SECRET", "")

# ─── Admin Panel ─────────────────────────────────────────────────────────────
ADMIN_USERNAME  = "admin"
ADMIN_PASSWORD  = "admin123"
SECRET_KEY      = os.environ.get("SECRET_KEY", "hamza-super-secret-xyz-2026")
FLASK_PORT      = int(os.environ.get("PORT", 5000))

# ─── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_REFER_COMMISSION = 10
MIN_WITHDRAWAL           = 100
MAX_WITHDRAWAL           = 50000

RULES_TEXT = """📋 *Shop Rules & Guidelines*

━━━━━━━━━━━━━━━━━━━━━
1️⃣ All payments are *non-refundable* once processed.
2️⃣ Delivery time: *1–24 hours* after payment confirmation.
3️⃣ Always provide *correct* game UID / account details.
4️⃣ Customer support is available *24/7*.
5️⃣ Refer & Earn: *10% commission* on every referral purchase.
6️⃣ Wallet balance can be used to pay for any order.
7️⃣ Minimum withdrawal: ₹*100* | Maximum: ₹*50,000*
8️⃣ Withdrawals are processed within *24–48 hours*.
━━━━━━━━━━━━━━━━━━━━━
⚠️ We are *not responsible* for wrong UID / details submitted by users."""
