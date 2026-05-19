# 🛍️ HamzaShop Telegram Bot + Admin Panel

A full-featured Telegram shop bot with Flask admin panel.  
**No Firebase SDK, no service.json, no API key** — uses only Firebase Realtime Database REST API (`/.json`).

---

## 📁 File Structure

```
telegram_bot/
├── app.py              # Flask server + admin panel routes
├── bot.py              # Telegram bot (all features)
├── config.py           # All config (env vars)
├── firebase_helper.py  # Firebase REST helper (get/put/patch/post/delete)
├── requirements.txt
├── Procfile            # For Render / Heroku
├── render.yaml         # Render one-click deploy config
└── templates/
    ├── base.html       # Admin layout
    ├── login.html
    ├── dashboard.html
    ├── orders.html
    ├── products.html
    ├── product_form.html
    ├── users.html
    ├── edit_user.html
    ├── categories.html
    ├── withdrawals.html
    ├── deposits.html
    ├── referrals.html
    ├── broadcast.html
    └── settings.html
```

---

## 🔥 Firebase Setup (NO SDK — REST Only)

1. Go to [Firebase Console](https://console.firebase.google.com)
2. Create a project → **Realtime Database** → Create database
3. Set rules to allow read/write (for testing):
   ```json
   {
     "rules": {
       ".read": true,
       ".write": true
     }
   }
   ```
4. Copy your Database URL:  
   `https://your-project-default-rtdb.firebaseio.com`

**For production rules (secured):**
1. Get a DB Secret: Project Settings → Service Accounts → Database Secrets
2. Add it as `FIREBASE_SECRET` env variable
3. Set rules to:
   ```json
   {
     "rules": {
       ".read": "auth != null",
       ".write": "auth != null"
     }
   }
   ```

---

## ⚙️ Environment Variables

| Variable          | Required | Example                                              |
|-------------------|----------|------------------------------------------------------|
| `BOT_TOKEN`       | ✅       | `7123456789:AAFxxxx`                                 |
| `BOT_USERNAME`    | ✅       | `MyShopBot`                                          |
| `FIREBASE_URL`    | ✅       | `https://myproject-rtdb.firebaseio.com`              |
| `FIREBASE_SECRET` | ❌       | DB legacy secret (only if rules are not public)      |
| `PAYMENT_LINK`    | ❌       | UPI or payment gateway URL                           |
| `SECRET_KEY`      | ❌       | Flask session secret (auto-generated on Render)      |
| `PORT`            | ❌       | 5000 (set automatically by Render)                   |

---

## 🚀 Deploy on Render (Free)

1. Push this folder to a GitHub repo
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. **Build Command:** `pip install -r requirements.txt`
5. **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
6. Add Environment Variables in Render dashboard
7. Deploy!

Admin panel will be at: `https://your-app.onrender.com`  
Admin login: **admin / admin123**

---

## 🤖 Bot Features

- ✅ Channel membership verification on `/start`
- ✅ Referral system with 10% commission (configurable)
- ✅ Shop with categories: FF Diamond, PUBG UC, Mobile, Laptop, Other
- ✅ Paginated product listings
- ✅ Add to cart + full checkout
- ✅ Pay from wallet or online (UTR submission)
- ✅ Wallet: deposit, withdraw, history
- ✅ Purchase history
- ✅ Order confirmation with admin verification
- ✅ Custom product input fields (UID, email, mobile, etc.)
- ✅ Delivery info collection for physical products

---

## 🖥️ Admin Panel Features

- 📊 Dashboard with live stats, charts, top users
- 📦 Orders: filter by date/status, approve/reject, add notes
- 🏷️ Products: add/edit/delete, custom fields, image preview
- 🗂️ Categories: add/edit/delete custom categories
- 💵 Deposits: approve → auto-credit wallet
- 💸 Withdrawals: approve/reject with refund
- 👥 Users: search, edit wallet, delete
- 🔗 Referrals: view all, top referrers, commission tracking
- 📢 Broadcast: send text/image/both to all users
- ⚙️ Settings: commission %, payment link, support username, rules

---

## 🏦 Firebase Data Structure

```
/users/{chat_id}/
/orders/{order_id}/
/products/{product_id}/
/categories/{category_id}/
/carts/{chat_id}/
/transactions/{txn_id}/      ← deposits
/withdrawals/{withdrawal_id}/
/referrals/{referrer_id}/{referred_id}/
/refer_codes/{code} → chat_id
/category_views/{category}/
/settings/
```

---

## 💳 Add Payment Gateway Later

Edit `config.py`:
```python
PAYMENT_LINK = "https://razorpay.com/pay/your-link"
```
Or set the `PAYMENT_LINK` environment variable on Render.

---

## 🔐 Change Admin Credentials

Edit `config.py`:
```python
ADMIN_USERNAME = "your_username"
ADMIN_PASSWORD = "your_password"
```
