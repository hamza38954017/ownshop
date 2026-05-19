"""
app.py  —  HamzaShop Admin Panel + Flask server
Runs the Telegram bot in a background thread so one process handles both.
Deploy on Render as a Web Service (free tier).
"""

from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, flash)
from functools import wraps
import threading, datetime, json
import firebase_helper as fb
from config import (ADMIN_USERNAME, ADMIN_PASSWORD, SECRET_KEY,
                    FLASK_PORT, DEFAULT_REFER_COMMISSION)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ─────────────────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == ADMIN_USERNAME and
                request.form.get("password") == ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Invalid credentials"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def today_str():
    return datetime.date.today().isoformat()

def _orders_list():
    raw = fb.get("orders") or {}
    return [{"_id": k, **v} for k, v in raw.items() if isinstance(v, dict)]

def _users_list():
    raw = fb.get("users") or {}
    return [{"_id": k, **v} for k, v in raw.items() if isinstance(v, dict)]

def _products_list():
    raw = fb.get("products") or {}
    return [{"_id": k, **v} for k, v in raw.items() if isinstance(v, dict)]

def _categories_list():
    raw = fb.get("categories") or {}
    return [{"_id": k, **v} for k, v in raw.items() if isinstance(v, dict)]

def _withdrawals_list():
    raw = fb.get("withdrawals") or {}
    return [{"_id": k, **v} for k, v in raw.items() if isinstance(v, dict)]

def _transactions_list():
    raw = fb.get("transactions") or {}
    return [{"_id": k, **v} for k, v in raw.items() if isinstance(v, dict)]

def _stats():
    orders  = _orders_list()
    users   = _users_list()
    prods   = _products_list()
    td      = today_str()

    today_orders    = [o for o in orders if o.get("created_at","").startswith(td)]
    pending_orders  = [o for o in orders if o.get("payment_status") == "pending"]
    failed_orders   = [o for o in orders if o.get("payment_status") == "failed"]
    total_revenue   = sum(o.get("price", o.get("total", 0)) for o in orders
                          if o.get("payment_status") == "success")
    today_revenue   = sum(o.get("price", o.get("total", 0)) for o in today_orders
                          if o.get("payment_status") == "success")
    today_new_users = [u for u in users if u.get("created_at","").startswith(td)]
    today_active    = [u for u in users if u.get("last_seen","").startswith(td)]

    # top wallet users
    top_wallet = sorted(users, key=lambda u: u.get("wallet", 0), reverse=True)[:10]
    # top referrers
    top_refer  = sorted(users, key=lambda u: u.get("refer_count", 0), reverse=True)[:10]

    withdrawals = _withdrawals_list()
    total_withdrawn = sum(w.get("amount", 0) for w in withdrawals if w.get("status") == "success")
    pending_withdraw = [w for w in withdrawals if w.get("status") == "pending"]

    # category views
    cat_views = fb.get("category_views") or {}

    return {
        "total_users":       len(users),
        "today_new_users":   len(today_new_users),
        "today_active":      len(today_active),
        "total_orders":      len(orders),
        "today_orders":      len(today_orders),
        "pending_orders":    len(pending_orders),
        "failed_orders":     len(failed_orders),
        "total_revenue":     total_revenue,
        "today_revenue":     today_revenue,
        "total_products":    len(prods),
        "top_wallet_users":  top_wallet,
        "top_referrers":     top_refer,
        "total_withdrawn":   total_withdrawn,
        "pending_withdrawals": len(pending_withdraw),
        "cat_views":         cat_views,
        "commission":        fb.get_setting("refer_commission", DEFAULT_REFER_COMMISSION),
    }

# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    stats = _stats()
    return render_template("dashboard.html", stats=stats)

# ─────────────────────────────────────────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/orders")
@login_required
def orders():
    all_orders = _orders_list()
    # date filter
    date_from = request.args.get("from", "")
    date_to   = request.args.get("to",   "")
    status    = request.args.get("status", "")
    if date_from:
        all_orders = [o for o in all_orders if o.get("created_at","") >= date_from]
    if date_to:
        all_orders = [o for o in all_orders if o.get("created_at","") <= date_to + "T99"]
    if status:
        all_orders = [o for o in all_orders if o.get("payment_status") == status]
    all_orders.sort(key=lambda o: o.get("created_at",""), reverse=True)
    return render_template("orders.html", orders=all_orders,
                           date_from=date_from, date_to=date_to, status=status)

@app.route("/orders/<order_id>/update", methods=["POST"])
@login_required
def update_order(order_id):
    data = request.form
    patch = {
        "payment_status": data.get("payment_status", "pending"),
        "order_status":   data.get("order_status",   "pending"),
        "updated_at":     datetime.datetime.now().isoformat(),
    }
    note = data.get("note","")
    if note:
        patch["admin_note"] = note
    fb.patch(f"orders/{order_id}", patch)

    # If approved → credit referral commission
    if patch["payment_status"] == "success":
        order = fb.get(f"orders/{order_id}")
        if order:
            cid = order.get("chat_id","")
            user = fb.get(f"users/{cid}")
            if user:
                referred_by = user.get("referred_by","")
                price = order.get("price", order.get("total", 0))
                if referred_by:
                    commission = fb.get_setting("refer_commission", DEFAULT_REFER_COMMISSION)
                    earned = round(price * commission / 100, 2)
                    ref_user = fb.get(f"users/{referred_by}")
                    if ref_user:
                        fb.patch(f"users/{referred_by}", {
                            "wallet":       ref_user.get("wallet", 0) + earned,
                            "total_earned": ref_user.get("total_earned", 0) + earned,
                            "verified_refer": ref_user.get("verified_refer", 0) + 1,
                            "pending_refer":  max(ref_user.get("pending_refer", 0) - 1, 0),
                        })
                        fb.patch(f"referrals/{referred_by}/{cid}", {
                            "status": "verified", "earned": earned})
                # Update user history
                fb.patch(f"users/{cid}/purchase_history/{order_id}", {"status": "success"})

    flash(f"Order {order_id} updated.", "success")
    return redirect(url_for("orders"))

@app.route("/orders/<order_id>/delete", methods=["POST"])
@login_required
def delete_order(order_id):
    fb.delete(f"orders/{order_id}")
    flash(f"Order {order_id} deleted.", "warning")
    return redirect(url_for("orders"))

# ─────────────────────────────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/users")
@login_required
def users():
    all_users = _users_list()
    q = request.args.get("q","").lower()
    if q:
        all_users = [u for u in all_users
                     if q in u.get("full_name","").lower()
                     or q in u.get("username","").lower()
                     or q in str(u.get("chat_id","")).lower()]
    all_users.sort(key=lambda u: u.get("created_at",""), reverse=True)
    return render_template("users.html", users=all_users, q=q)

@app.route("/users/<cid>/edit", methods=["GET","POST"])
@login_required
def edit_user(cid):
    u = fb.get(f"users/{cid}")
    if not u:
        flash("User not found", "danger")
        return redirect(url_for("users"))
    if request.method == "POST":
        fb.patch(f"users/{cid}", {
            "wallet":    float(request.form.get("wallet", u.get("wallet",0))),
            "full_name": request.form.get("full_name", u.get("full_name","")),
            "verified":  request.form.get("verified") == "1",
        })
        flash("User updated.", "success")
        return redirect(url_for("users"))
    return render_template("edit_user.html", user=u, cid=cid)

@app.route("/users/<cid>/delete", methods=["POST"])
@login_required
def delete_user(cid):
    fb.delete(f"users/{cid}")
    flash("User deleted.", "warning")
    return redirect(url_for("users"))

# ─────────────────────────────────────────────────────────────────────────────
# PRODUCTS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/products")
@login_required
def products():
    prods = _products_list()
    cats  = _categories_list()
    cat_filter = request.args.get("category","")
    if cat_filter:
        prods = [p for p in prods if p.get("category") == cat_filter]
    prods.sort(key=lambda p: p.get("name",""))
    return render_template("products.html", products=prods, categories=cats,
                           cat_filter=cat_filter)

@app.route("/products/add", methods=["GET","POST"])
@login_required
def add_product():
    cats = _categories_list()
    if request.method == "POST":
        fields_raw = request.form.get("custom_fields","")
        custom_fields = []
        for line in fields_raw.strip().splitlines():
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 2:
                custom_fields.append({
                    "label":    parts[0],
                    "type":     parts[1] if len(parts) > 1 else "text",
                    "required": parts[2].lower() == "yes" if len(parts) > 2 else True,
                    "validate": parts[3] if len(parts) > 3 else "",
                })
        data = {
            "name":        request.form.get("name",""),
            "description": request.form.get("description",""),
            "price":       float(request.form.get("price", 0)),
            "stock":       int(request.form.get("stock", 999)),
            "category":    request.form.get("category",""),
            "image_url":   request.form.get("image_url",""),
            "game_info":   request.form.get("game_info",""),
            "active":      request.form.get("active") == "1",
            "fields":      custom_fields,
            "views":       0,
            "created_at":  datetime.datetime.now().isoformat(),
        }
        fb.post("products", data)
        flash("Product added!", "success")
        return redirect(url_for("products"))
    return render_template("product_form.html", product=None, categories=cats, action="Add")

@app.route("/products/<pid>/edit", methods=["GET","POST"])
@login_required
def edit_product(pid):
    p = fb.get(f"products/{pid}")
    cats = _categories_list()
    if not p:
        flash("Product not found", "danger")
        return redirect(url_for("products"))
    if request.method == "POST":
        fields_raw = request.form.get("custom_fields","")
        custom_fields = []
        for line in fields_raw.strip().splitlines():
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 2:
                custom_fields.append({
                    "label":    parts[0],
                    "type":     parts[1] if len(parts) > 1 else "text",
                    "required": parts[2].lower() == "yes" if len(parts) > 2 else True,
                    "validate": parts[3] if len(parts) > 3 else "",
                })
        fb.patch(f"products/{pid}", {
            "name":        request.form.get("name",""),
            "description": request.form.get("description",""),
            "price":       float(request.form.get("price", 0)),
            "stock":       int(request.form.get("stock", 999)),
            "category":    request.form.get("category",""),
            "image_url":   request.form.get("image_url",""),
            "game_info":   request.form.get("game_info",""),
            "active":      request.form.get("active") == "1",
            "fields":      custom_fields,
        })
        flash("Product updated!", "success")
        return redirect(url_for("products"))
    return render_template("product_form.html", product={"_id": pid, **p},
                           categories=cats, action="Edit")

@app.route("/products/<pid>/delete", methods=["POST"])
@login_required
def delete_product(pid):
    fb.delete(f"products/{pid}")
    flash("Product deleted.", "warning")
    return redirect(url_for("products"))

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/categories")
@login_required
def categories():
    cats = _categories_list()
    return render_template("categories.html", categories=cats)

@app.route("/categories/add", methods=["POST"])
@login_required
def add_category():
    fb.post("categories", {
        "name":       request.form.get("name",""),
        "emoji":      request.form.get("emoji","🏷️"),
        "active":     True,
        "created_at": datetime.datetime.now().isoformat(),
    })
    flash("Category added!", "success")
    return redirect(url_for("categories"))

@app.route("/categories/<cid>/edit", methods=["POST"])
@login_required
def edit_category(cid):
    fb.patch(f"categories/{cid}", {
        "name":   request.form.get("name",""),
        "emoji":  request.form.get("emoji","🏷️"),
        "active": request.form.get("active") == "1",
    })
    flash("Category updated.", "success")
    return redirect(url_for("categories"))

@app.route("/categories/<cid>/delete", methods=["POST"])
@login_required
def delete_category(cid):
    fb.delete(f"categories/{cid}")
    flash("Category deleted.", "warning")
    return redirect(url_for("categories"))

# ─────────────────────────────────────────────────────────────────────────────
# WITHDRAWALS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/withdrawals")
@login_required
def withdrawals():
    ws = _withdrawals_list()
    ws.sort(key=lambda w: w.get("created_at",""), reverse=True)
    return render_template("withdrawals.html", withdrawals=ws)

@app.route("/withdrawals/<wid>/update", methods=["POST"])
@login_required
def update_withdrawal(wid):
    status = request.form.get("status","pending")
    note   = request.form.get("note","")
    fb.patch(f"withdrawals/{wid}", {
        "status":     status,
        "note":       note,
        "updated_at": datetime.datetime.now().isoformat(),
    })
    # If rejected → refund wallet
    if status == "failed":
        w = fb.get(f"withdrawals/{wid}")
        if w:
            cid = w.get("chat_id","")
            amount = w.get("amount", 0)
            u = fb.get(f"users/{cid}")
            if u:
                fb.patch(f"users/{cid}", {"wallet": u.get("wallet", 0) + amount})
    flash(f"Withdrawal {wid} → {status}.", "success")
    return redirect(url_for("withdrawals"))

# ─────────────────────────────────────────────────────────────────────────────
# DEPOSITS / TRANSACTIONS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/deposits")
@login_required
def deposits():
    txns = _transactions_list()
    txns = [t for t in txns if t.get("type") == "deposit"]
    txns.sort(key=lambda t: t.get("created_at",""), reverse=True)
    return render_template("deposits.html", deposits=txns)

@app.route("/deposits/<tid>/approve", methods=["POST"])
@login_required
def approve_deposit(tid):
    txn = fb.get(f"transactions/{tid}")
    if txn and txn.get("status") != "success":
        cid    = txn.get("chat_id","")
        amount = txn.get("amount", 0)
        u = fb.get(f"users/{cid}")
        if u:
            fb.patch(f"users/{cid}", {
                "wallet":        u.get("wallet", 0) + amount,
                "total_deposit": u.get("total_deposit", 0) + amount,
            })
        fb.patch(f"transactions/{tid}", {
            "status":     "success",
            "updated_at": datetime.datetime.now().isoformat(),
        })
    flash("Deposit approved & wallet credited.", "success")
    return redirect(url_for("deposits"))

@app.route("/deposits/<tid>/reject", methods=["POST"])
@login_required
def reject_deposit(tid):
    fb.patch(f"transactions/{tid}", {
        "status":     "failed",
        "note":       request.form.get("note",""),
        "updated_at": datetime.datetime.now().isoformat(),
    })
    flash("Deposit rejected.", "warning")
    return redirect(url_for("deposits"))

# ─────────────────────────────────────────────────────────────────────────────
# BROADCAST
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/broadcast", methods=["GET","POST"])
@login_required
def broadcast():
    result = None
    if request.method == "POST":
        text      = request.form.get("message","").strip()
        image_url = request.form.get("image_url","").strip()
        if not text and not image_url:
            result = {"error": "Please enter a message or image URL."}
        else:
            try:
                import bot as telegram_bot
                ok, fail = telegram_bot.broadcast_message(
                    text=text or None,
                    image_url=image_url or None
                )
                result = {"ok": ok, "fail": fail}
            except Exception as e:
                result = {"error": str(e)}
    return render_template("broadcast.html", result=result)

# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    if request.method == "POST":
        fb.set_setting("refer_commission", float(request.form.get("commission", DEFAULT_REFER_COMMISSION)))
        fb.set_setting("payment_link",     request.form.get("payment_link",""))
        fb.set_setting("support_username", request.form.get("support_username",""))
        fb.set_setting("rules_text",       request.form.get("rules_text",""))
        flash("Settings saved!", "success")

    commission    = fb.get_setting("refer_commission", DEFAULT_REFER_COMMISSION)
    payment_link  = fb.get_setting("payment_link","")
    support_user  = fb.get_setting("support_username","")
    rules_text    = fb.get_setting("rules_text","")
    return render_template("settings.html",
                           commission=commission,
                           payment_link=payment_link,
                           support_username=support_user,
                           rules_text=rules_text)

# ─────────────────────────────────────────────────────────────────────────────
# REFERRALS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/referrals")
@login_required
def referrals():
    users = _users_list()
    ref_data = []
    for u in users:
        cid = u.get("_id","")
        refs = fb.get(f"referrals/{cid}") or {}
        ref_data.append({
            "user": u,
            "count": u.get("refer_count",0),
            "earned": u.get("total_earned",0),
            "verified": u.get("verified_refer",0),
            "pending": u.get("pending_refer",0),
        })
    ref_data.sort(key=lambda x: x["count"], reverse=True)
    total_commission = sum(r["earned"] for r in ref_data)
    return render_template("referrals.html", referrals=ref_data,
                           total_commission=total_commission)

# ─────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS (for AJAX in admin panel)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(_stats())

@app.route("/api/user/<cid>/wallet", methods=["POST"])
@login_required
def api_adjust_wallet(cid):
    amount = float(request.json.get("amount", 0))
    u = fb.get(f"users/{cid}")
    if not u:
        return jsonify({"error": "User not found"}), 404
    new_bal = u.get("wallet", 0) + amount
    fb.patch(f"users/{cid}", {"wallet": new_bal})
    return jsonify({"wallet": new_bal})

@app.route("/api/products/<pid>/stock", methods=["POST"])
@login_required
def api_update_stock(pid):
    stock = int(request.json.get("stock", 0))
    fb.patch(f"products/{pid}", {"stock": stock})
    return jsonify({"stock": stock})

# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK  (Render keeps the service alive with ping)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "HamzaShop"})

# ─────────────────────────────────────────────────────────────────────────────
# BOT STARTUP  — runs under both gunicorn and plain `python app.py`
# ─────────────────────────────────────────────────────────────────────────────
import os as _os

_BOT_TOKEN_PLACEHOLDER = "YOUR_BOT_TOKEN_HERE"
_bot_token = _os.environ.get("BOT_TOKEN", _BOT_TOKEN_PLACEHOLDER)

if _bot_token and _bot_token != _BOT_TOKEN_PLACEHOLDER:
    try:
        import bot as telegram_bot
        _bot_thread = threading.Thread(target=telegram_bot.run_bot, daemon=True)
        _bot_thread.start()
        print("🤖 Telegram bot thread started.")
    except Exception as _e:
        print(f"⚠️  Bot thread failed to start: {_e}")
else:
    print("⚠️  BOT_TOKEN not set — Telegram bot will not run.")

# ─────────────────────────────────────────────────────────────────────────────
# LOCAL DEV ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 Flask admin starting on port {FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
