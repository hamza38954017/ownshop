"""
app.py — HamzaShop Flask Admin Panel
All credentials stored in Firebase /config
"""
from flask import (Flask,render_template,request,redirect,url_for,
                   session,flash,jsonify,Response)
from functools import wraps
import datetime, json, threading
import firebase_helper as fb
from config import SECRET_KEY, FLASK_PORT, ADMIN_USERNAME, ADMIN_PASSWORD, PANEL_NAME
from support_routes_add import support_bp
import datetime as _dt

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.register_blueprint(support_bp)

# ── Auth ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*a,**kw):
        if not session.get("admin"): return redirect(url_for("login"))
        return f(*a,**kw)
    return decorated

def now_str(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@app.context_processor
def inject_globals():
    return {"panel_name":PANEL_NAME(), "year":datetime.datetime.now().year}

# ── Login ─────────────────────────────────────────────────────────────────────
@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        entered_user = request.form.get("username","").strip()
        entered_pass = request.form.get("password","").strip()
        # Read credentials — env var first, then Firebase, then hardcoded default
        import os as _os
        valid_user = (_os.environ.get("ADMIN_USERNAME","").strip()
                      or fb.get_setting("admin_username","")
                      or "admin")
        valid_pass = (_os.environ.get("ADMIN_PASSWORD","").strip()
                      or fb.get_setting("admin_password","")
                      or "admin123")
        if entered_user == valid_user and entered_pass == valid_pass:
            session["admin"] = True
            return redirect(url_for("dashboard"))
        flash("Invalid credentials","danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    users    = fb.get("users") or {}
    orders   = fb.get("orders") or {}
    deposits = fb.get("deposit_sessions") or {}
    products = fb.get("products") or {}

    total_revenue = sum((o.get("price") or o.get("total") or 0) for o in orders.values()
                        if o.get("payment_status")=="success")
    total_pending_orders = sum(1 for o in orders.values() if o.get("order_status")=="pending")
    total_revenue_today = sum(
        (o.get("price") or o.get("total") or 0)
        for o in orders.values()
        if o.get("payment_status")=="success" and
           (o.get("created_at","") or "")[:10]==datetime.date.today().isoformat()
    )
    recent_orders = sorted(orders.items(),key=lambda x:x[1].get("created_at",""),reverse=True)[:8]

    stats = {
        "users": len(users),
        "orders": len(orders),
        "products": len(products),
        "revenue": total_revenue,
        "revenue_today": total_revenue_today,
        "pending_orders": total_pending_orders,
        "deposits": sum(1 for d in deposits.values() if d.get("status")=="pending"),
    }
    return render_template("dashboard.html",stats=stats,recent_orders=recent_orders)

# ── Orders ────────────────────────────────────────────────────────────────────
@app.route("/orders")
@login_required
def orders():
    orders = fb.get("orders") or {}
    date_from = request.args.get("from","")
    date_to   = request.args.get("to","")
    status    = request.args.get("status","")
    all_orders = []
    for oid,o in orders.items():
        if not o.get("price"): o["price"] = o.get("total",0)
        o["_id"] = oid
        if date_from and o.get("created_at","")[:10] < date_from: continue
        if date_to   and o.get("created_at","")[:10] > date_to:   continue
        if status and o.get("order_status") != status: continue
        all_orders.append(o)
    all_orders.sort(key=lambda o:o.get("created_at",""),reverse=True)
    return render_template("orders.html",orders=all_orders,
                           date_from=date_from,date_to=date_to,status=status)

@app.route("/orders/<oid>/update",methods=["POST"])
@login_required
def update_order(oid):
    new_status = request.form.get("order_status","")
    fb.patch(f"orders/{oid}",{"order_status":new_status,"updated_at":now_str()})
    # notify user via bot
    try:
        import bot as telegram_bot
        telegram_bot.notify_order_status(oid,new_status)
    except Exception as e:
        print(f"[notify] {e}")
    flash(f"Order {oid} updated to {new_status}","success")
    return redirect(url_for("orders"))

@app.route("/orders/<oid>/delete",methods=["POST"])
@login_required
def delete_order(oid):
    fb.delete(f"orders/{oid}")
    flash("Order deleted","success")
    return redirect(url_for("orders"))

# ── Products ──────────────────────────────────────────────────────────────────
@app.route("/products")
@login_required
def products():
    prods = fb.get_list("products")
    cats  = fb.get("categories") or {}
    cat_map = {k:v.get("name",k) for k,v in cats.items()}
    for p in prods:
        p["category_name"] = cat_map.get(p.get("category",""),"—")
    return render_template("products.html",products=prods)

@app.route("/products/add",methods=["GET","POST"])
@login_required
def add_product():
    cats = fb.get_list("categories")
    fields_defs = fb.get("field_definitions") or {}
    if request.method=="POST":
        fval = lambda k,d="": request.form.get(k,d).strip()
        selected_field_ids = request.form.getlist("product_fields")
        fields = []
        for fid in selected_field_ids:
            fd = fields_defs.get(fid)
            if fd: fields.append({"id":fid,**fd})
        data = {
            "name":fval("name"),"description":fval("description"),
            "price":float(fval("price",0) or 0),
            "stock":int(fval("stock",999) or 999),
            "category":fval("category"),
            "active":fval("active","1")=="1",
            "image_url":fval("image_url"),
            "fields":fields,
            "views":0,"created_at":now_str()
        }
        fb.post("products",data)
        flash("Product added","success"); return redirect(url_for("products"))
    return render_template("product_form.html",action="Add",product=None,
                           categories=cats,field_definitions=fields_defs)

@app.route("/products/<pid>/edit",methods=["GET","POST"])
@login_required
def edit_product(pid):
    cats = fb.get_list("categories")
    fields_defs = fb.get("field_definitions") or {}
    p = fb.get(f"products/{pid}")
    if not p: flash("Not found","danger"); return redirect(url_for("products"))
    p["_id"]=pid
    if request.method=="POST":
        fval = lambda k,d="": request.form.get(k,d).strip()
        selected_field_ids = request.form.getlist("product_fields")
        fields = []
        for fid in selected_field_ids:
            fd = fields_defs.get(fid)
            if fd: fields.append({"id":fid,**fd})
        data = {
            "name":fval("name"),"description":fval("description"),
            "price":float(fval("price",0) or 0),
            "stock":int(fval("stock",999) or 999),
            "category":fval("category"),
            "active":fval("active","1")=="1",
            "image_url":fval("image_url"),
            "fields":fields,"updated_at":now_str()
        }
        fb.patch(f"products/{pid}",data)
        flash("Product updated","success"); return redirect(url_for("products"))
    return render_template("product_form.html",action="Edit",product=p,
                           categories=cats,field_definitions=fields_defs)

@app.route("/products/<pid>/delete",methods=["POST"])
@login_required
def delete_product(pid):
    fb.delete(f"products/{pid}")
    flash("Product deleted","success"); return redirect(url_for("products"))

@app.route("/products/<pid>/stock",methods=["POST"])
@login_required
def update_stock(pid):
    stock = int(request.form.get("stock",0))
    fb.patch(f"products/{pid}",{"stock":stock})
    return jsonify({"stock":stock})

# ── Categories ────────────────────────────────────────────────────────────────
@app.route("/categories")
@login_required
def categories():
    cats = fb.get_list("categories")
    return render_template("categories.html",categories=cats)

@app.route("/categories/add",methods=["POST"])
@login_required
def add_category():
    name  = request.form.get("name","").strip()
    emoji = request.form.get("emoji","🏷️").strip()
    if name:
        fb.post("categories",{"name":name,"emoji":emoji,"active":True,"created_at":now_str()})
        flash("Category added","success")
    return redirect(url_for("categories"))

@app.route("/categories/<cid>/edit",methods=["POST"])
@login_required
def edit_category(cid):
    fb.patch(f"categories/{cid}",{
        "name":request.form.get("name","").strip(),
        "emoji":request.form.get("emoji","🏷️").strip(),
        "active":request.form.get("active","1")=="1"
    })
    flash("Category updated","success"); return redirect(url_for("categories"))

@app.route("/categories/<cid>/delete",methods=["POST"])
@login_required
def delete_category(cid):
    fb.delete(f"categories/{cid}")
    flash("Category deleted","success"); return redirect(url_for("categories"))

# ── Field Definitions ─────────────────────────────────────────────────────────
@app.route("/fields")
@login_required
def field_definitions():
    fields = fb.get("field_definitions") or {}
    return render_template("fields.html",fields=fields)

@app.route("/fields/add",methods=["POST"])
@login_required
def add_field():
    label = request.form.get("label","").strip()
    ftype = request.form.get("type","text")
    required = request.form.get("required","1")=="1"
    max_length = int(request.form.get("max_length",200) or 200)
    validate = request.form.get("validate","").strip()
    if label:
        fb.post("field_definitions",{
            "label":label,"type":ftype,"required":required,
            "max_length":max_length,"validate":validate,"created_at":now_str()
        })
        flash("Field added","success")
    return redirect(url_for("field_definitions"))

@app.route("/fields/<fid>/delete",methods=["POST"])
@login_required
def delete_field(fid):
    fb.delete(f"field_definitions/{fid}")
    flash("Field deleted","success"); return redirect(url_for("field_definitions"))

# ── Deposits ──────────────────────────────────────────────────────────────────
@app.route("/deposits")
@login_required
def deposits():
    sessions = fb.get("deposit_sessions") or {}
    all_deps = []
    users = fb.get("users") or {}
    for sid,s in sessions.items():
        cid = s.get("chat_id","")
        u = users.get(cid,{})
        s["_id"]=sid; s["user_name"]=u.get("full_name",cid)
        all_deps.append(s)
    all_deps.sort(key=lambda x:x.get("created_at",""),reverse=True)
    return render_template("deposits.html",deposits=all_deps)

@app.route("/deposits/<did>/approve",methods=["POST"])
@login_required
def approve_deposit(did):
    s = fb.get(f"deposit_sessions/{did}")
    if s and s.get("status")!="completed":
        cid = s.get("chat_id"); amount = s.get("amount",0)
        u = fb.get(f"users/{cid}") or {}
        fb.patch(f"users/{cid}",{"wallet":u.get("wallet",0)+amount,
                                   "total_deposit":u.get("total_deposit",0)+amount})
        fb.patch(f"deposit_sessions/{did}",{"status":"completed","approved_at":now_str()})
        fb.patch(f"users/{cid}/transactions/{did}",{"status":"success"})
        try:
            import bot as b
            b.send_msg(cid,
                f"✅ *Deposit Approved!*\n"
                f"💰 ₹{amount} added to your wallet.\n"
                f"👛 New balance: ₹{u.get('wallet',0)+amount}")
        except: pass
        flash(f"₹{amount} credited to user","success")
    return redirect(url_for("deposits"))

@app.route("/deposits/<did>/reject",methods=["POST"])
@login_required
def reject_deposit(did):
    s = fb.get(f"deposit_sessions/{did}")
    if s:
        fb.patch(f"deposit_sessions/{did}",{"status":"failed"})
        fb.patch(f"users/{s.get('chat_id','')}/transactions/{did}",{"status":"failed"})
        flash("Deposit rejected","warning")
    return redirect(url_for("deposits"))

# ── Withdrawals ───────────────────────────────────────────────────────────────
@app.route("/withdrawals")
@login_required
def withdrawals():
    wds = fb.get_list("withdrawals")
    wds.sort(key=lambda x:x.get("created_at",""),reverse=True)
    return render_template("withdrawals.html",withdrawals=wds)

@app.route("/withdrawals/<wid>/approve",methods=["POST"])
@login_required
def approve_withdrawal(wid):
    wd = fb.get(f"withdrawals/{wid}")
    if wd:
        fb.patch(f"withdrawals/{wid}",{"status":"success","approved_at":now_str()})
        fb.patch(f"users/{wd.get('chat_id','')}/transactions/{wid}",{"status":"success"})
        try:
            import bot as b
            b.send_msg(wd.get("chat_id"),
                f"✅ *Withdrawal Approved!*\n💰 ₹{wd.get('amount',0)} sent to {wd.get('account','')}")
        except: pass
        flash("Withdrawal approved","success")
    return redirect(url_for("withdrawals"))

@app.route("/withdrawals/<wid>/reject",methods=["POST"])
@login_required
def reject_withdrawal(wid):
    wd = fb.get(f"withdrawals/{wid}")
    if wd:
        # Refund wallet
        cid = wd.get("chat_id",""); amount = wd.get("amount",0)
        u = fb.get(f"users/{cid}") or {}
        fb.patch(f"users/{cid}",{"wallet":u.get("wallet",0)+amount})
        fb.patch(f"withdrawals/{wid}",{"status":"failed"})
        fb.patch(f"users/{cid}/transactions/{wid}",{"status":"failed"})
        try:
            import bot as b
            b.send_msg(cid,f"❌ *Withdrawal Rejected.*\n₹{amount} refunded to your wallet.")
        except: pass
        flash("Withdrawal rejected and refunded","warning")
    return redirect(url_for("withdrawals"))

@app.route("/withdrawals/<wid>/delete",methods=["POST"])
@login_required
def delete_withdrawal(wid):
    fb.delete(f"withdrawals/{wid}")
    flash("Deleted","success"); return redirect(url_for("withdrawals"))

# ── Users ─────────────────────────────────────────────────────────────────────
@app.route("/users")
@login_required
def users():
    all_users = fb.get_list("users")
    q = request.args.get("q","").lower()
    if q:
        all_users = [u for u in all_users if q in u.get("full_name","").lower()
                     or q in u.get("username","").lower()
                     or q in str(u.get("chat_id","")).lower()]
    all_users.sort(key=lambda u:u.get("created_at",""),reverse=True)
    return render_template("users.html",users=all_users,q=q)

@app.route("/users/<uid>/edit",methods=["GET","POST"])
@login_required
def edit_user(uid):
    u = fb.get(f"users/{uid}")
    if not u: flash("Not found","danger"); return redirect(url_for("users"))
    u["_id"]=uid
    if request.method=="POST":
        fb.patch(f"users/{uid}",{
            "wallet":float(request.form.get("wallet",0) or 0),
            "full_name":request.form.get("full_name","").strip(),
        })
        flash("User updated","success"); return redirect(url_for("users"))
    return render_template("edit_user.html",user=u)

@app.route("/users/<uid>/delete",methods=["POST"])
@login_required
def delete_user(uid):
    fb.delete(f"users/{uid}"); flash("User deleted","success")
    return redirect(url_for("users"))

@app.route("/users/<uid>/message",methods=["POST"])
@login_required
def message_user(uid):
    text = request.form.get("message","").strip()
    if text:
        try:
            import bot as b; b.send_msg(uid,text)
            flash("Message sent","success")
        except Exception as e:
            flash(str(e),"danger")
    return redirect(url_for("users"))

# ── Referrals ─────────────────────────────────────────────────────────────────
@app.route("/referrals")
@login_required
def referrals():
    all_refs = []
    users = fb.get("users") or {}
    for uid,u in users.items():
        refs = fb.get(f"referrals/{uid}") or {}
        for rid,r in refs.items():
            all_refs.append({"referrer":u.get("full_name",uid),
                             "referrer_id":uid,**r})
    all_refs.sort(key=lambda x:x.get("joined_at",""),reverse=True)
    return render_template("referrals.html",referrals=all_refs)

# ── Broadcast ─────────────────────────────────────────────────────────────────
@app.route("/broadcast",methods=["GET","POST"])
@login_required
def broadcast():
    result = None
    if request.method=="POST":
        text      = request.form.get("message","").strip()
        image_url = request.form.get("image_url","").strip()
        if not text and not image_url:
            result = {"error":"Enter a message or image URL."}
        else:
            try:
                import bot as tbot
                ok,fail = tbot.broadcast_message(text=text or None,image_url=image_url or None)
                # Store broadcast
                fb.post("broadcasts",{
                    "text":text,"image_url":image_url,
                    "ok":ok,"fail":fail,"sent_at":now_str()
                })
                result = {"ok":ok,"fail":fail}
            except Exception as e:
                result = {"error":str(e)}
    broadcasts = fb.get_list("broadcasts")
    broadcasts.sort(key=lambda x:x.get("sent_at",""),reverse=True)
    return render_template("broadcast.html",result=result,broadcasts=broadcasts)

@app.route("/broadcast/<bid>/delete",methods=["POST"])
@login_required
def delete_broadcast(bid):
    fb.delete(f"broadcasts/{bid}")
    flash("Broadcast deleted","success"); return redirect(url_for("broadcast"))

# ── Custom Payment ────────────────────────────────────────────────────────────
@app.route("/payments")
@login_required
def custom_payments():
    sessions = fb.get("payment_sessions") or {}
    dep_sess = fb.get("deposit_sessions") or {}
    all_pay = []
    for sid,s in {**sessions,**dep_sess}.items():
        s["_id"]=sid
        all_pay.append(s)
    all_pay.sort(key=lambda x:x.get("created_at",""),reverse=True)
    return render_template("payments.html",payments=all_pay)

@app.route("/payments/create",methods=["GET","POST"])
@login_required
def create_payment():
    import kimipay as kp
    result = None
    if request.method=="POST":
        amount = int(request.form.get("amount",0) or 0)
        desc   = request.form.get("description","Admin Payment").strip()
        if amount < 100:
            flash("Minimum amount is ₹100","danger")
        else:
            order_sn = "ADM"+"".join(__import__("random").choices("0123456789",k=8))
            res = kp.create_order(amount=amount,order_sn=order_sn,description=desc)
            if res.get("error"):
                flash(res["error"],"danger")
            else:
                fb.put(f"payment_sessions/{order_sn}",{
                    "order_sn":order_sn,"amount":amount,
                    "kimipay_order_id":res["kimipay_order_id"],
                    "payment_url":res["payment_url"],
                    "description":desc,"status":"pending",
                    "type":"custom","created_at":now_str()
                })
                result = {"url":res["payment_url"],"order_sn":order_sn}
    return render_template("create_payment.html",result=result)

@app.route("/payments/<sid>/check",methods=["POST"])
@login_required
def check_payment_status(sid):
    import kimipay as kp
    s = fb.get(f"payment_sessions/{sid}") or fb.get(f"deposit_sessions/{sid}")
    if not s: return jsonify({"error":"Not found"})
    res = kp.query_order(s.get("kimipay_order_id",""))
    if res.get("success"):
        status = res.get("status","pending")
        fb.patch(f"payment_sessions/{sid}",{"status":status})
        return jsonify({"status":status,"amount":res.get("amount")})
    return jsonify({"status":"unknown","error":res.get("error","")})

# ── KimiPay Callback ──────────────────────────────────────────────────────────
@app.route("/callback/kimipay",methods=["POST"])
def kimipay_callback():
    try:
        data = request.get_json(force=True) or request.form.to_dict()
        import bot as tbot
        tbot.handle_kimipay_callback(data)
    except Exception as e:
        print(f"[Callback] {e}")
    return "ok",200

# ── Settings ──────────────────────────────────────────────────────────────────
@app.route("/settings",methods=["GET","POST"])
@login_required
def settings():
    if request.method=="POST":
        # Keys saved always (blank is valid for these)
        normal_keys = [
            "bot_token","bot_username","support_username","support_bot_token","support_greeting","support_auto_reply",
            "payment_link","rules_text",
            "panel_name","panel_copyright",
            "refer_commission","min_deposit","min_withdrawal","max_withdrawal",
            "kimipay_app_id","kimipay_api_key","kimipay_base_url",
            "notify_chat_ids",
        ]
        for k in normal_keys:
            val = request.form.get(k,"")
            fb.set_setting(k, val)

        # Admin username — only save if not blank
        new_username = request.form.get("admin_username","").strip()
        if new_username:
            fb.set_setting("admin_username", new_username)

        # Admin password — only save if not blank (blank = keep current)
        new_password = request.form.get("admin_password","").strip()
        if new_password:
            fb.set_setting("admin_password", new_password)
        else:
            flash("Settings saved (password unchanged)","success")
            return redirect(url_for("settings"))

        flash("Settings saved","success")
        return redirect(url_for("settings"))
    cfg = fb.get("config") or {}
    return render_template("settings.html",cfg=cfg)

# ── Health ──────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status":"ok"})

# ── Bot Startup ───────────────────────────────────────────────────────────────
import os as _os, threading as _th

def _start_bot():
    try:
        import bot as tbot
        if tbot.bot:
            _th.Thread(target=tbot.run_bot,daemon=True).start()
            print("🤖 Shop bot thread started")
        else:
            print("⚠️ Shop bot token missing — check BOT_TOKEN env var or Firebase /config/bot_token")
    except Exception as e:
        print(f"⚠️ Bot thread error: {e}")

def _start_support_bot():
    try:
        import support_bot as sb
        if sb.support_bot:
            _th.Thread(target=sb.run_support_bot,daemon=True).start()
            print("🎧 Support bot thread started")
        else:
            print("⚠️ Support bot token missing — set SUPPORT_BOT_TOKEN env var or Firebase /config/support_bot_token")
    except Exception as e:
        print(f"⚠️ Support bot thread error: {e}")

_start_bot()
_start_support_bot()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=FLASK_PORT,debug=False)
