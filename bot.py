"""
HamzaShop Telegram Bot — bot.py
BOT_TOKEN read from env variable first, fallback to Firebase /config/bot_token
"""
import telebot
from telebot import types
import datetime, random, string, time, re, threading, os
import firebase_helper as fb
import kimipay
from config import (BOT_TOKEN, BOT_USERNAME, SUPPORT_USERNAME, PAYMENT_LINK,
                    RULES_TEXT, MIN_DEPOSIT, MIN_WITHDRAWAL, MAX_WITHDRAWAL,
                    REFER_COMMISSION, NOTIFY_CHAT_IDS)

def _get_token():
    # 1. Environment variable (fastest, works on Render immediately)
    token = os.environ.get("BOT_TOKEN", "").strip()
    if token:
        return token
    # 2. Fallback: Firebase /config/bot_token
    token = BOT_TOKEN()
    if token:
        return token
    print("⚠️  BOT_TOKEN not found in env or Firebase /config/bot_token")
    return None

_BOT_TOKEN = _get_token()
bot = telebot.TeleBot(_BOT_TOKEN, parse_mode=None) if _BOT_TOKEN else None

# ── State (in-memory per gunicorn worker) ────────────────────────────────────
user_states = {}
user_temp   = {}

def get_state(cid):   return user_states.get(str(cid))
def set_state(cid,s): user_states[str(cid)] = s
def clear_state(cid): user_states.pop(str(cid),None); user_temp.pop(str(cid),None)
def get_temp(cid):    return user_temp.get(str(cid),{})
def set_temp(cid,d):  user_temp[str(cid)] = d
def upd_temp(cid,d):  user_temp.setdefault(str(cid),{}).update(d)

# ── Helpers ───────────────────────────────────────────────────────────────────
def now_str(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def now_dt():  return datetime.datetime.now()
def gen_code(n=8): return "".join(random.choices(string.ascii_uppercase+string.digits,k=n))
def gen_order_id(): return "ORD"+"".join(random.choices(string.digits,k=8))
def fmt_price(p):
    try: return f"₹{float(p):,.0f}"
    except: return f"₹{p}"

def paginate(items,page,per_page=5):
    start=page*per_page
    return items[start:start+per_page], len(items)>start+per_page, page>0

def send_notify(text):
    """Send to all admin notification chat IDs."""
    for cid in NOTIFY_CHAT_IDS():
        try: bot.send_message(cid, text, parse_mode="Markdown")
        except: pass

def send_msg(cid, text, **kw):
    try: bot.send_message(cid, text, parse_mode="Markdown", **kw)
    except Exception as e: print(f"[MSG] {cid}: {e}")

# ── User helpers ──────────────────────────────────────────────────────────────
def get_user(cid): return fb.get(f"users/{cid}")

def ensure_user(message, referred_by=None):
    cid = str(message.chat.id)
    u = fb.get(f"users/{cid}")
    if u:
        fb.patch(f"users/{cid}", {"last_seen": now_str()})
        return u, False
    rc = gen_code()
    while fb.get(f"refer_codes/{rc}"): rc = gen_code()
    fn = (message.from_user.first_name or "").strip()
    ln = (message.from_user.last_name  or "").strip()
    data = {
        "chat_id":cid, "full_name":f"{fn} {ln}".strip(),
        "first_name":fn, "last_name":ln,
        "username":message.from_user.username or "",
        "refer_code":rc, "referred_by":referred_by or "",
        "wallet":0, "total_earned":0,
        "verified_refer":0, "pending_refer":0, "refer_count":0,
        "total_spent":0, "total_deposit":0,
        "purchase_count":0, "verified":True,
        "created_at":now_str(), "last_seen":now_str(),
    }
    fb.put(f"users/{cid}", data)
    fb.put(f"refer_codes/{rc}", cid)
    if referred_by and referred_by != cid:
        ref = fb.get(f"users/{referred_by}")
        if ref:
            fb.patch(f"users/{referred_by}", {
                "pending_refer": ref.get("pending_refer",0)+1,
                "refer_count":   ref.get("refer_count",0)+1,
            })
            fb.put(f"referrals/{referred_by}/{cid}", {
                "chat_id":cid,"name":data["full_name"],
                "status":"pending","joined_at":now_str(),"earned":0
            })
            send_msg(referred_by,
                f"🎉 *New Referral!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *{data['full_name']}* just joined using your referral link!\n"
                f"🤝 They'll earn you commission on every purchase.\n"
                f"💸 Keep sharing your link!")
    return data, True

# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_main():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("🏠 Home", "🛍️ Shop")
    m.add("🛒 My Cart", "💳 Transactions")
    m.add("👥 Refer & Earn", "👤 My Referrals")
    m.add("💰 Wallet", "📋 Rules")
    m.add("📞 Support")
    return m

def kb_cancel():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("❌ Cancel")
    return m

def kb_categories():
    m = types.InlineKeyboardMarkup(row_width=2)
    cats = fb.get("categories") or {}
    btns = []
    for cid, cd in cats.items():
        if cd.get("active", True):
            btns.append(types.InlineKeyboardButton(
                f"{cd.get('emoji','🏷️')} {cd['name']}",
                callback_data=f"cat_{cid}"))
    if not btns:
        btns.append(types.InlineKeyboardButton("🏪 No categories yet", callback_data="home"))
    for i in range(0,len(btns),2): m.row(*btns[i:i+2])
    m.add(types.InlineKeyboardButton("🏠 Home", callback_data="home"))
    return m

def kb_back(cbd="shop"):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("🔙 Back", callback_data=cbd))
    return m

# ── Guard ─────────────────────────────────────────────────────────────────────
def _guard(msg):
    cid = str(msg.chat.id)
    if not fb.get(f"users/{cid}"):
        cmd_start(msg); return False
    fb.patch(f"users/{cid}", {"last_seen":now_str()})
    return True

def _guard_cancel(msg):
    """Returns True if message is cancel command."""
    return msg.text and msg.text.strip() == "❌ Cancel"

# ── /start ────────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    cid = str(msg.chat.id)
    args = msg.text.split()
    ref_by = None
    if len(args) > 1:
        ref = fb.get(f"refer_codes/{args[1]}")
        if ref and str(ref) != cid: ref_by = str(ref)

    u, is_new = ensure_user(msg, ref_by)
    greet = (
        f"🎉 *Welcome to the Shop!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Hello *{u.get('first_name','Friend')}*!\n\n"
        f"🛍️ Browse products, top up games,\n"
        f"💰 earn wallet balance through referrals!\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Wallet: *{fmt_price(u.get('wallet',0))}*\n"
        f"🤝 Commission: *{REFER_COMMISSION()}%* per referral purchase\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 Use the menu below:"
    ) if is_new else (
        f"🏠 *Welcome Back!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Hey *{u.get('first_name','Friend')}*!\n"
        f"💵 Wallet: *{fmt_price(u.get('wallet',0))}*\n"
        f"🛒 Orders: *{u.get('purchase_count',0)}*"
    )
    send_msg(cid, greet, reply_markup=kb_main())
    # show shop
    bot.send_message(cid,
        "🛍️ *Shop — Choose a Category*\n━━━━━━━━━━━━━━━━━━━━━\n👇 Select below:",
        parse_mode="Markdown", reply_markup=kb_categories())

# ── Home ──────────────────────────────────────────────────────────────────────
def send_home(cid):
    u = get_user(cid)
    if not u: return
    send_msg(cid,
        f"🏠 *Home*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *{u.get('full_name','Friend')}*\n"
        f"💵 Wallet: *{fmt_price(u.get('wallet',0))}*\n"
        f"🛒 Orders: *{u.get('purchase_count',0)}*\n"
        f"🤝 Referrals: *{u.get('refer_count',0)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text=="🏠 Home")
def msg_home(msg):
    if not _guard(msg): return
    send_home(str(msg.chat.id))

@bot.callback_query_handler(func=lambda c: c.data=="home")
def cb_home(c):
    bot.answer_callback_query(c.id)
    send_home(str(c.message.chat.id))

# ── Shop / Categories ─────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text in ("🛍️ Shop","🛒 Shop Now"))
def msg_shop(msg):
    if not _guard(msg): return
    bot.send_message(str(msg.chat.id),
        "🛍️ *Shop — Categories*\n━━━━━━━━━━━━━━━━━━━━━\n👇 Choose:",
        parse_mode="Markdown", reply_markup=kb_categories())

@bot.callback_query_handler(func=lambda c: c.data=="shop")
def cb_shop(c):
    bot.answer_callback_query(c.id)
    try:
        bot.edit_message_text("🛍️ *Shop — Categories*\n━━━━━━━━━━━━━━━━━━━━━\n👇 Choose:",
            str(c.message.chat.id), c.message.message_id,
            parse_mode="Markdown", reply_markup=kb_categories())
    except:
        bot.send_message(str(c.message.chat.id),
            "🛍️ *Shop — Categories*", parse_mode="Markdown", reply_markup=kb_categories())

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def cb_category(c):
    bot.answer_callback_query(c.id)
    cid = str(c.message.chat.id)
    cat_key = c.data[4:]  # strip "cat_"
    # Fetch category name from DB
    cat_data = fb.get(f"categories/{cat_key}") or {}
    cat_name = f"{cat_data.get('emoji','🏷️')} {cat_data.get('name', cat_key)}" if cat_data else cat_key
    send_products(cid, c.message.message_id, cat_key, 0, cat_name)

def send_products(cid, mid, cat_key, page=0, cat_name="Products"):
    products = fb.get("products") or {}
    cat_products = [(pid,pd) for pid,pd in products.items()
                    if pd.get("category")==cat_key and pd.get("active",True)]
    if not cat_products:
        try:
            bot.edit_message_text(
                f"🏪 *{cat_name}*\n━━━━━━━━━━━━━━━━━━━━━\n❌ No products in this category yet.",
                cid, mid, parse_mode="Markdown", reply_markup=kb_back("shop"))
        except: pass
        return

    items, has_next, has_prev = paginate(cat_products, page)
    mk = types.InlineKeyboardMarkup(row_width=1)
    for pid, pd in items:
        stock = pd.get("stock",999)
        stock_tag = f" ✅" if stock>0 else " ❌"
        mk.add(types.InlineKeyboardButton(
            f"{pd.get('name','?')} — {fmt_price(pd.get('price',0))}{stock_tag}",
            callback_data=f"prod_{pid}~{cat_key}"))

    nav = []
    if has_prev: nav.append(types.InlineKeyboardButton("⬅️ Prev", callback_data=f"pg|{cat_key}|{page-1}"))
    if has_next: nav.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"pg|{cat_key}|{page+1}"))
    if nav: mk.row(*nav)
    mk.add(types.InlineKeyboardButton("🔙 Categories", callback_data="shop"))

    text = (f"🏪 *{cat_name}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 {len(cat_products)} product(s) | Page {page+1}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ = In stock  ❌ = Out of stock\n"
            f"👇 Select a product:")
    try:
        bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=mk)
    except:
        bot.send_message(cid, text, parse_mode="Markdown", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith("pg|"))
def cb_page(c):
    bot.answer_callback_query(c.id)
    _, cat_key, page = c.data.split("|",2)
    cat_data = fb.get(f"categories/{cat_key}") or {}
    cat_name = f"{cat_data.get('emoji','🏷️')} {cat_data.get('name',cat_key)}" if cat_data else cat_key
    send_products(str(c.message.chat.id), c.message.message_id, cat_key, int(page), cat_name)

# ── Product Detail ────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("prod_"))
def cb_product(c):
    bot.answer_callback_query(c.id)
    # format: prod_<pid>~<cat_key>
    payload = c.data[5:]
    pid, cat_key = (payload.split("~",1)+[""])[:2]
    cid = str(c.message.chat.id)
    p = fb.get(f"products/{pid}")
    if not p:
        bot.answer_callback_query(c.id,"❌ Product not found",show_alert=True); return

    fb.patch(f"products/{pid}",{"views":p.get("views",0)+1})
    stock = p.get("stock",999)

    text = (
        f"🏷️ *{p.get('name','')}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 {p.get('description','')}\n\n"
        f"💰 Price: *{fmt_price(p.get('price',0))}*\n"
        f"📦 Stock: *{'✅ Available' if stock>0 else '❌ Out of Stock'}*"
        + (f" ({stock})" if 0<stock<50 else "") +
        f"\n━━━━━━━━━━━━━━━━━━━━━"
    )

    mk = types.InlineKeyboardMarkup(row_width=2)
    if stock > 0:
        mk.add(
            types.InlineKeyboardButton("🛒 Add to Cart", callback_data=f"addcart_{pid}~{cat_key}"),
            types.InlineKeyboardButton("⚡ Buy Now",     callback_data=f"buynow_{pid}"),
        )
    # Back goes to category list — store cat_key so back works
    back_cb = f"cat_{cat_key}" if cat_key else "shop"
    mk.add(types.InlineKeyboardButton("🔙 Back", callback_data=back_cb))

    if p.get("image_url"):
        try:
            bot.send_photo(cid, p["image_url"], caption=text, parse_mode="Markdown", reply_markup=mk)
            return
        except: pass
    try:
        bot.edit_message_text(text, cid, c.message.message_id, parse_mode="Markdown", reply_markup=mk)
    except:
        bot.send_message(cid, text, parse_mode="Markdown", reply_markup=mk)

# ── Add to Cart ───────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("addcart_"))
def cb_add_cart(c):
    payload = c.data[8:]
    pid, cat_key = (payload.split("~",1)+[""])[:2]
    cid = str(c.message.chat.id)
    p = fb.get(f"products/{pid}")
    if not p:
        bot.answer_callback_query(c.id,"❌ Not found",show_alert=True); return
    cart = fb.get(f"carts/{cid}") or {}
    if pid in cart:
        cart[pid]["qty"] = cart[pid].get("qty",1)+1
    else:
        cart[pid] = {"product_id":pid,"name":p["name"],"price":p["price"],
                     "qty":1,"category":p.get("category",""),
                     "fields":p.get("fields",[]),"added_at":now_str()}
    fb.put(f"carts/{cid}", cart)
    bot.answer_callback_query(c.id, f"✅ {p['name']} added to cart!", show_alert=True)

# ── View Cart ─────────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text=="🛒 My Cart")
def msg_cart(msg):
    if not _guard(msg): return
    _show_cart(str(msg.chat.id))

@bot.callback_query_handler(func=lambda c: c.data=="view_cart")
def cb_view_cart(c):
    bot.answer_callback_query(c.id)
    _show_cart(str(c.message.chat.id), c.message.message_id)

def _show_cart(cid, mid=None):
    cart = fb.get(f"carts/{cid}") or {}
    if not cart:
        text = "🛒 *My Cart*\n━━━━━━━━━━━━━━━━━━━━━\n🈳 Your cart is empty!\n\n👉 Go to Shop to add items."
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("🛍️ Shop Now", callback_data="shop"))
        if mid:
            try: bot.edit_message_text(text,cid,mid,parse_mode="Markdown",reply_markup=mk); return
            except: pass
        bot.send_message(cid,text,parse_mode="Markdown",reply_markup=mk); return

    total = sum(v["price"]*v.get("qty",1) for v in cart.values())
    lines = ["🛒 *My Cart*\n━━━━━━━━━━━━━━━━━━━━━"]
    for i,(pid,item) in enumerate(cart.items(),1):
        lines.append(f"{i}. *{item['name']}*\n   {item.get('qty',1)} × {fmt_price(item['price'])} = *{fmt_price(item['price']*item.get('qty',1))}*")
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━\n💰 *Total: {fmt_price(total)}*")
    mk = types.InlineKeyboardMarkup(row_width=1)
    for pid,item in cart.items():
        mk.add(types.InlineKeyboardButton(f"❌ Remove {item['name'][:18]}", callback_data=f"rmcart_{pid}"))
    mk.add(types.InlineKeyboardButton("🗑️ Clear Cart", callback_data="clear_cart"),
           types.InlineKeyboardButton("💳 Checkout",   callback_data="checkout_cart"))
    mk.add(types.InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop"))
    text = "\n".join(lines)
    if mid:
        try: bot.edit_message_text(text,cid,mid,parse_mode="Markdown",reply_markup=mk); return
        except: pass
    bot.send_message(cid,text,parse_mode="Markdown",reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith("rmcart_"))
def cb_rm_cart(c):
    pid = c.data[7:]; cid = str(c.message.chat.id)
    cart = fb.get(f"carts/{cid}") or {}
    cart.pop(pid,None); fb.put(f"carts/{cid}",cart)
    bot.answer_callback_query(c.id,"🗑️ Removed")
    _show_cart(cid, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data=="clear_cart")
def cb_clear_cart(c):
    fb.delete(f"carts/{str(c.message.chat.id)}")
    bot.answer_callback_query(c.id,"🗑️ Cart cleared!")
    _show_cart(str(c.message.chat.id), c.message.message_id)

# ── Checkout Cart ─────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data=="checkout_cart")
def cb_checkout_cart(c):
    bot.answer_callback_query(c.id)
    cid = str(c.message.chat.id)
    cart = fb.get(f"carts/{cid}") or {}
    if not cart:
        bot.answer_callback_query(c.id,"🛒 Cart is empty!",show_alert=True); return
    total = sum(float(v.get("price",0))*int(v.get("qty",1)) for v in cart.values())
    u = get_user(cid)
    # Store everything including explicit amount so it survives field collection
    set_temp(cid, {"is_cart":True,"cart_items":dict(cart),
                   "cart_total":total,"amount":total,"field_queue":[]})
    set_state(cid,"wait_field")
    _start_cart_fields(cid, dict(cart), total, c.message.message_id, u)

def _start_cart_fields(cid, cart, total, mid, u):
    """Build a queue of (item_name, field) pairs for all cart items that need fields."""
    queue = []
    for pid, item in cart.items():
        fields = item.get("fields",[])
        if not fields:
            # Fetch from DB in case cart was added before fields were defined
            p = fb.get(f"products/{pid}") or {}
            fields = p.get("fields",[])
        for f in fields:
            queue.append({"pid":pid,"item_name":item["name"],"field":f,"answer":""})
    upd_temp(cid,{"field_queue":queue,"field_index":0,"field_answers":{}})
    if queue:
        _ask_next_field(cid)
    else:
        # No fields needed — go to payment
        _show_checkout_payment(cid, total, u)

def _ask_next_field(cid):
    temp = get_temp(cid)
    queue = temp.get("field_queue",[])
    idx = temp.get("field_index",0)
    if idx >= len(queue):
        # All fields collected — use stored amount (set at start of buynow/checkout)
        amount = temp.get("amount") or                  temp.get("cart_total",0) if temp.get("is_cart")                  else temp.get("amount") or temp.get("product",{}).get("price",0)
        if not amount:
            # Last resort re-derive
            amount = temp.get("cart_total",0) or temp.get("product",{}).get("price",0)
        clear_state(cid)
        u = get_user(cid)
        _show_checkout_payment(cid, float(amount), u)
        return
    entry = queue[idx]
    f = entry["field"]
    label = f.get("label","Enter value")
    ftype = f.get("type","text")
    req_star = " *" if f.get("required") else ""
    set_state(cid,"wait_field")
    send_msg(cid,
        f"📝 *{entry['item_name']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Field {idx+1}/{len(queue)}\n\n"
        f"Please enter: *{label}*{req_star}\n"
        + (f"_(type: {ftype})_" if ftype not in ("text","string") else ""),
        reply_markup=types.ForceReply(selective=True))

def _show_checkout_payment(cid, amount, u):
    temp = get_temp(cid)
    wallet = u.get("wallet",0) if u else 0
    is_cart = temp.get("is_cart",False)
    if is_cart:
        cart_items = temp.get("cart_items",{})
        items_txt = "\n".join(f"  • {v.get('name','')} ×{v.get('qty',1)}" for v in cart_items.values())
        summary = f"🛒 *Cart Items:*\n{items_txt}\n\n💰 *Total: {fmt_price(amount)}*"
    else:
        p = temp.get("product",{})
        summary = f"📦 *{p.get('name','')}*\n💰 *Price: {fmt_price(amount)}*"

    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton("💳 Pay Online (KimiPay)", callback_data="pay_kimipay"),
           types.InlineKeyboardButton("👛 Pay from Wallet",      callback_data=f"pay_wallet"))
    mk.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_order"))

    set_state(cid,"choose_payment")
    send_msg(cid,
        f"💳 *Checkout*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{summary}\n"
        f"👛 Wallet Balance: *{fmt_price(wallet)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose payment method:",
        reply_markup=mk)

# ── Buy Now ───────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("buynow_"))
def cb_buy_now(c):
    bot.answer_callback_query(c.id)
    pid = c.data[7:]; cid = str(c.message.chat.id)
    p = fb.get(f"products/{pid}")
    if not p: bot.answer_callback_query(c.id,"❌ Not found",show_alert=True); return
    set_temp(cid,{"product_id":pid,"product":p,"is_cart":False,"field_index":0,
                   "field_answers":{},"field_queue":[]})
    # Build field queue from product fields
    fields = p.get("fields",[])
    queue = [{"pid":pid,"item_name":p["name"],"field":f,"answer":""} for f in fields]
    upd_temp(cid,{"field_queue":queue,"field_index":0})
    if queue:
        _ask_next_field(cid)
    else:
        u = get_user(cid)
        _show_checkout_payment(cid, p.get("price",0), u)

# ── Pay with Wallet ───────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data=="pay_wallet")
def cb_pay_wallet(c):
    bot.answer_callback_query(c.id)
    cid = str(c.message.chat.id)
    temp = get_temp(cid)
    u = get_user(cid)
    is_cart = temp.get("is_cart",False)
    # Use explicitly stored amount first
    amount = float(temp.get("amount") or
                   (temp.get("cart_total",0) if is_cart
                    else temp.get("product",{}).get("price",0)) or 0)
    wallet = float(u.get("wallet",0))
    if amount <= 0:
        send_msg(cid, "❌ *Payment Error*\nCould not determine order amount. Please go back and try again.", reply_markup=kb_main()); return
    if wallet < amount:
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(types.InlineKeyboardButton("➕ Add Money", callback_data="wallet_deposit"),
               types.InlineKeyboardButton("❌ Cancel",    callback_data="cancel_order"))
        send_msg(cid,
            f"❌ *Insufficient Wallet Balance!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Order Amount: *{fmt_price(amount)}*\n"
            f"👛 Your Balance: *{fmt_price(wallet)}*\n"
            f"📉 Shortfall: *{fmt_price(amount - wallet)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"➕ Add money to your wallet to continue.",
            reply_markup=mk); return
    _place_order(cid, "wallet", amount, None)

# ── Pay with KimiPay ──────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data=="pay_kimipay")
def cb_pay_kimipay(c):
    bot.answer_callback_query(c.id)
    cid = str(c.message.chat.id)
    temp = get_temp(cid)
    is_cart = temp.get("is_cart",False)
    amount = int(float(temp.get("amount") or
                       (temp.get("cart_total",0) if is_cart
                        else temp.get("product",{}).get("price",0)) or 0))
    order_sn = gen_order_id()
    u = get_user(cid)
    # Create KimiPay order
    result = kimipay.create_order(
        amount=amount, order_sn=order_sn,
        description="HamzaShop Order",
        customer_email=temp.get("field_answers",{}).get("email",""))
    if result.get("error"):
        # Fallback to manual payment link
        plink = PAYMENT_LINK()
        mk = types.InlineKeyboardMarkup()
        if plink: mk.add(types.InlineKeyboardButton("💳 Pay Now", url=plink))
        mk.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_order"))
        send_msg(cid,
            f"⚠️ Auto-payment unavailable.\n{result['error']}\n\n"
            f"Please pay manually and contact support.",
            reply_markup=mk)
        return

    pay_url = result["payment_url"]
    kimipay_order_id = result["kimipay_order_id"]
    # Store payment session in Firebase
    upd_temp(cid,{"order_sn":order_sn,"kimipay_order_id":kimipay_order_id,"amount":amount})
    fb.put(f"payment_sessions/{order_sn}",{
        "chat_id":cid,"order_sn":order_sn,
        "kimipay_order_id":kimipay_order_id,
        "amount":amount,"status":"pending",
        "temp":temp,"created_at":now_str()
    })

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("💳 Pay Now", url=pay_url))
    mk.add(types.InlineKeyboardButton("✅ I've Paid — Verify", callback_data=f"verify_pay_{order_sn}"))
    mk.add(types.InlineKeyboardButton("❌ Cancel Payment", callback_data="cancel_order"))
    set_state(cid,"wait_payment")
    send_msg(cid,
        f"💳 *Complete Your Payment*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Amount: *{fmt_price(amount)}*\n"
        f"🔖 Order: `{order_sn}`\n\n"
        f"1️⃣ Click *Pay Now* to open payment page\n"
        f"2️⃣ Complete payment on KimiPay\n"
        f"3️⃣ Click *I've Paid* to verify\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Do not close this screen until payment is done.",
        reply_markup=mk)

# ── Verify Payment ────────────────────────────────────────────────────────────
_verify_attempts = {}  # order_sn → count

@bot.callback_query_handler(func=lambda c: c.data.startswith("verify_pay_"))
def cb_verify_pay(c):
    bot.answer_callback_query(c.id,"🔍 Checking payment...")
    cid = str(c.message.chat.id)
    order_sn = c.data[11:]
    temp = get_temp(cid)
    kimipay_id = temp.get("kimipay_order_id") or ""

    # Rate limit
    attempts = _verify_attempts.get(order_sn,0)
    if attempts >= 8:
        bot.answer_callback_query(c.id,
            "❌ Too many attempts. Please contact support.",show_alert=True); return
    _verify_attempts[order_sn] = attempts+1

    if not kimipay_id:
        # Try fetching from DB
        sess = fb.get(f"payment_sessions/{order_sn}") or {}
        kimipay_id = sess.get("kimipay_order_id","")

    result = kimipay.query_order(kimipay_id)
    status = result.get("status","pending")

    if status in ("success","paid","completed"):
        _verify_attempts.pop(order_sn,None)
        amount = temp.get("amount",0) or result.get("amount",0)
        _place_order(cid,"kimipay",amount,order_sn)
    elif result.get("error"):
        bot.answer_callback_query(c.id,
            f"⚠️ Could not verify: {result['error'][:60]}",show_alert=True)
    else:
        remaining = 8-(_verify_attempts.get(order_sn,0))
        bot.answer_callback_query(c.id,
            f"⏳ Payment not confirmed yet. ({remaining} attempts left)\nTry again after completing payment.",
            show_alert=True)

# ── KimiPay Callback ──────────────────────────────────────────────────────────
def handle_kimipay_callback(data:dict):
    """Called by Flask webhook route."""
    order_sn   = data.get("order_sn","")
    status     = data.get("status","")
    amount     = data.get("amount",0)
    if status not in ("success","paid","completed"): return "ok"
    sess = fb.get(f"payment_sessions/{order_sn}")
    if not sess: return "ok"
    if sess.get("status")=="completed": return "ok"
    fb.patch(f"payment_sessions/{order_sn}",{"status":"completed"})
    cid = sess.get("chat_id")
    # Restore temp
    saved_temp = sess.get("temp",{})
    set_temp(cid,saved_temp)
    upd_temp(cid,{"amount":amount,"order_sn":order_sn})
    _place_order(cid,"kimipay",amount,order_sn)
    return "ok"

# ── Place Order ───────────────────────────────────────────────────────────────
def _place_order(cid, payment_method, amount, order_sn=None):
    temp = get_temp(cid)
    u = get_user(cid)
    is_cart = temp.get("is_cart",False)
    field_answers = temp.get("field_answers",{})
    order_id = order_sn or gen_order_id()

    if is_cart:
        cart_items = temp.get("cart_items",{})
        order = {
            "order_id":order_id,"chat_id":cid,
            "user_name":u.get("full_name",""),"user_id":cid,
            "items":cart_items,"total":amount,"price":amount,
            "payment_method":payment_method,
            "payment_status":"success" if payment_method in ("kimipay","wallet") else "pending",
            "order_status":"processing" if payment_method in ("kimipay","wallet") else "pending",
            "field_answers":field_answers,
            "created_at":now_str(),"updated_at":now_str(),
        }
        fb.put(f"orders/{order_id}",order)
        fb.delete(f"carts/{cid}")
        fb.put(f"users/{cid}/transactions/{order_id}",{
            "type":"purchase","for":f"{len(cart_items)} item(s)",
            "amount":-amount,"status":"success","date":now_str()
        })
    else:
        p = temp.get("product",{})
        order = {
            "order_id":order_id,"chat_id":cid,
            "user_name":u.get("full_name",""),"user_id":cid,
            "product_id":temp.get("product_id",""),
            "product_name":p.get("name",""),"price":amount,
            "category":p.get("category",""),
            "payment_method":payment_method,
            "payment_status":"success" if payment_method in ("kimipay","wallet") else "pending",
            "order_status":"processing" if payment_method in ("kimipay","wallet") else "pending",
            "field_answers":field_answers,
            "created_at":now_str(),"updated_at":now_str(),
        }
        fb.put(f"orders/{order_id}",order)
        fb.put(f"users/{cid}/transactions/{order_id}",{
            "type":"purchase","for":p.get("name",""),
            "amount":-amount,"status":"success","date":now_str()
        })

    # Deduct wallet if wallet payment
    if payment_method=="wallet":
        fb.patch(f"users/{cid}",{
            "wallet":max(0,u.get("wallet",0)-amount),
            "purchase_count":u.get("purchase_count",0)+1,
            "total_spent":u.get("total_spent",0)+amount,
        })
    else:
        fb.patch(f"users/{cid}",{
            "purchase_count":u.get("purchase_count",0)+1,
            "total_spent":u.get("total_spent",0)+amount,
        })

    # Referral commission
    referred_by = u.get("referred_by","")
    if referred_by:
        comm_pct = REFER_COMMISSION()
        earned = round(amount*comm_pct/100,2)
        ref_u = get_user(referred_by)
        if ref_u:
            fb.patch(f"users/{referred_by}",{
                "wallet":ref_u.get("wallet",0)+earned,
                "total_earned":ref_u.get("total_earned",0)+earned,
                "verified_refer":ref_u.get("verified_refer",0)+1,
                "pending_refer":max(0,ref_u.get("pending_refer",0)-1),
            })
            fb.put(f"users/{referred_by}/transactions/{order_id}_ref",{
                "type":"referral","for":u.get("full_name","User"),
                "amount":earned,"status":"success","date":now_str()
            })
            fb.patch(f"referrals/{referred_by}/{cid}",{"status":"verified","earned":
                fb.get(f"referrals/{referred_by}/{cid}/earned") or 0 + earned})
            send_msg(referred_by,
                f"💸 *Referral Commission Earned!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *{u.get('full_name','A user')}* made a purchase!\n"
                f"💰 You earned: *{fmt_price(earned)}*\n"
                f"👛 New wallet balance: *{fmt_price(ref_u.get('wallet',0)+earned)}*")

    clear_state(cid)
    if is_cart:
        product_line = f"🛒 Items: *{len(temp.get('cart_items',{}))}*"
    else:
        product_line = f"📦 *{temp.get('product',{}).get('name','')}*"

    send_msg(cid,
        f"✅ *Order Confirmed!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{product_line}\n"
        f"💰 Amount: *{fmt_price(amount)}*\n"
        f"💳 Method: *{payment_method.title()}*\n"
        f"📋 Order ID: `{order_id}`\n"
        f"⏳ Status: *Processing*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 We'll notify you on any update!",
        reply_markup=kb_main())

    # Notify admin
    send_notify(
        f"🛒 *New Order!*\n"
        f"👤 {u.get('full_name',cid)} | 💰 {fmt_price(amount)}\n"
        f"📋 `{order_id}` | 💳 {payment_method}")

# ── Cancel ────────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data=="cancel_order")
def cb_cancel(c):
    bot.answer_callback_query(c.id,"❌ Cancelled")
    cid = str(c.message.chat.id)
    clear_state(cid)
    try: bot.delete_message(cid,c.message.message_id)
    except: pass
    send_msg(cid,"❌ *Order cancelled.*\n\nReturn to menu:",reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text=="❌ Cancel")
def msg_cancel(msg):
    cid = str(msg.chat.id)
    clear_state(cid)
    send_msg(cid,"❌ *Cancelled.*",reply_markup=kb_main())

# ── Field State Handler ───────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: get_state(str(m.chat.id))=="wait_field")
def handle_field(msg):
    cid = str(msg.chat.id)
    if _guard_cancel(msg): msg_cancel(msg); return
    temp = get_temp(cid)
    queue = temp.get("field_queue",[])
    idx = temp.get("field_index",0)
    if idx >= len(queue):
        return
    entry = queue[idx]
    f = entry["field"]
    label = f.get("label","field")
    ftype = f.get("type","text")
    val = msg.text.strip() if msg.text else ""

    # Validate
    if f.get("required") and not val:
        send_msg(cid,f"❌ *{label}* is required. Please enter a value:",
            reply_markup=types.ForceReply(selective=True)); return
    if ftype=="email" and val and not re.match(r"^[^@]+@[^@]+\.[^@]+$",val):
        send_msg(cid,f"❌ Invalid email format. Enter a valid email for *{label}*:",
            reply_markup=types.ForceReply(selective=True)); return
    if ftype in ("mobile","phone") and val and not re.match(r"^\d{7,15}$",val):
        send_msg(cid,f"❌ Invalid mobile number. Digits only (7–15) for *{label}*:",
            reply_markup=types.ForceReply(selective=True)); return
    if ftype in ("uid","number") and val and not val.isdigit():
        send_msg(cid,f"❌ *{label}* must be numeric. Enter again:",
            reply_markup=types.ForceReply(selective=True)); return
    if ftype=="uid" and val and len(val)<6:
        send_msg(cid,f"❌ UID must be at least 6 digits. Enter your *{label}*:",
            reply_markup=types.ForceReply(selective=True)); return
    max_len = f.get("max_length",500)
    if val and len(val)>int(max_len):
        send_msg(cid,f"❌ *{label}* is too long (max {max_len} chars). Enter again:",
            reply_markup=types.ForceReply(selective=True)); return

    # Store answer
    answers = temp.get("field_answers",{})
    answers[label] = val
    upd_temp(cid,{"field_answers":answers,"field_index":idx+1})
    _ask_next_field(cid)

# ── Deposit ───────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data=="wallet_deposit")
def cb_deposit(c):
    bot.answer_callback_query(c.id)
    cid = str(c.message.chat.id)
    min_dep = MIN_DEPOSIT()
    set_state(cid,"wait_deposit_amount")
    send_msg(cid,
        f"💵 *Add Money to Wallet*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Minimum deposit: *{fmt_price(min_dep)}*\n\n"
        f"Enter amount to deposit (₹):",
        reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(str(m.chat.id))=="wait_deposit_amount")
def handle_deposit_amount(msg):
    cid = str(msg.chat.id)
    if _guard_cancel(msg): msg_cancel(msg); return
    if not msg.text or not msg.text.strip().isdigit():
        send_msg(cid,"❌ Enter a valid amount (numbers only):",
            reply_markup=types.ForceReply(selective=True)); return
    amount = int(msg.text.strip())
    min_dep = MIN_DEPOSIT()
    if amount < min_dep:
        send_msg(cid,f"❌ Minimum deposit is *{fmt_price(min_dep)}*. Enter a higher amount:",
            reply_markup=types.ForceReply(selective=True)); return

    order_sn = gen_order_id()
    result = kimipay.create_order(amount=amount,order_sn=order_sn,description="Wallet Deposit")
    if result.get("error"):
        plink = PAYMENT_LINK()
        mk = types.InlineKeyboardMarkup()
        if plink: mk.add(types.InlineKeyboardButton("💳 Pay Now",url=plink))
        mk.add(types.InlineKeyboardButton("❌ Cancel",callback_data="cancel_order"))
        set_state(cid,"manual_deposit")
        upd_temp(cid,{"deposit_amount":amount,"order_sn":order_sn})
        send_msg(cid,
            f"💳 *Deposit ₹{amount}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Auto payment unavailable. Pay manually:\n{result['error']}\n\n"
            f"After payment contact support.",reply_markup=mk); return

    pay_url = result["payment_url"]
    kimipay_id = result["kimipay_order_id"]
    upd_temp(cid,{"deposit_amount":amount,"order_sn":order_sn,"kimipay_order_id":kimipay_id})
    fb.put(f"deposit_sessions/{order_sn}",{
        "chat_id":cid,"amount":amount,"order_sn":order_sn,
        "kimipay_order_id":kimipay_id,"status":"pending","created_at":now_str()
    })
    fb.put(f"users/{cid}/transactions/{order_sn}",{
        "type":"deposit","for":"Wallet Top-up",
        "amount":amount,"status":"pending","date":now_str()
    })
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("💳 Pay Now",url=pay_url))
    mk.add(types.InlineKeyboardButton("✅ I've Paid — Verify",callback_data=f"verify_dep_{order_sn}"))
    mk.add(types.InlineKeyboardButton("❌ Cancel",callback_data="cancel_order"))
    set_state(cid,"wait_deposit_verify")
    send_msg(cid,
        f"💳 *Deposit ₹{amount}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"1️⃣ Click *Pay Now*\n"
        f"2️⃣ Complete payment\n"
        f"3️⃣ Click *I've Paid* to verify\n"
        f"🔖 Ref: `{order_sn}`",reply_markup=mk)

_dep_attempts = {}

@bot.callback_query_handler(func=lambda c: c.data.startswith("verify_dep_"))
def cb_verify_dep(c):
    bot.answer_callback_query(c.id,"🔍 Checking...")
    cid = str(c.message.chat.id)
    order_sn = c.data[11:]
    attempts = _dep_attempts.get(order_sn,0)
    if attempts >= 8:
        bot.answer_callback_query(c.id,"❌ Too many attempts. Contact support.",show_alert=True); return
    _dep_attempts[order_sn] = attempts+1

    sess = fb.get(f"deposit_sessions/{order_sn}") or {}
    kimipay_id = sess.get("kimipay_order_id","") or get_temp(cid).get("kimipay_order_id","")
    result = kimipay.query_order(kimipay_id)
    status = result.get("status","pending")

    if status in ("success","paid","completed"):
        _dep_attempts.pop(order_sn,None)
        amount = sess.get("amount",0) or result.get("amount",0)
        u = get_user(cid)
        new_bal = u.get("wallet",0)+amount
        fb.patch(f"users/{cid}",{"wallet":new_bal,"total_deposit":u.get("total_deposit",0)+amount})
        fb.patch(f"deposit_sessions/{order_sn}",{"status":"completed"})
        fb.patch(f"users/{cid}/transactions/{order_sn}",{"status":"success"})
        clear_state(cid)
        send_msg(cid,
            f"✅ *Deposit Successful!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Added: *{fmt_price(amount)}*\n"
            f"👛 New Balance: *{fmt_price(new_bal)}*",
            reply_markup=kb_main())
        send_notify(f"💰 *Deposit!* {u.get('full_name',cid)} deposited {fmt_price(amount)}")
    else:
        remaining = 8-_dep_attempts.get(order_sn,0)
        bot.answer_callback_query(c.id,
            f"⏳ Not confirmed yet. ({remaining} tries left)",show_alert=True)

# ── Withdraw ──────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data=="wallet_withdraw")
def cb_withdraw(c):
    bot.answer_callback_query(c.id)
    cid = str(c.message.chat.id)
    set_state(cid,"wait_withdraw_amount")
    send_msg(cid,
        f"🏦 *Withdraw from Wallet*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Minimum: *{fmt_price(MIN_WITHDRAWAL())}*\n"
        f"Maximum: *{fmt_price(MAX_WITHDRAWAL())}*\n\n"
        f"Enter withdrawal amount (₹):",
        reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(str(m.chat.id))=="wait_withdraw_amount")
def handle_withdraw_amt(msg):
    cid = str(msg.chat.id)
    if _guard_cancel(msg): msg_cancel(msg); return
    if not msg.text or not msg.text.strip().isdigit():
        send_msg(cid,"❌ Enter a valid amount:",reply_markup=types.ForceReply(selective=True)); return
    amount = int(msg.text.strip())
    u = get_user(cid)
    wallet = u.get("wallet",0)
    if amount < MIN_WITHDRAWAL():
        send_msg(cid,f"❌ Minimum is *{fmt_price(MIN_WITHDRAWAL())}*:",reply_markup=types.ForceReply(selective=True)); return
    if amount > MAX_WITHDRAWAL():
        send_msg(cid,f"❌ Maximum is *{fmt_price(MAX_WITHDRAWAL())}*:",reply_markup=types.ForceReply(selective=True)); return
    if amount > wallet:
        send_msg(cid,f"❌ Insufficient balance. Wallet: *{fmt_price(wallet)}*:",reply_markup=types.ForceReply(selective=True)); return
    upd_temp(cid,{"withdraw_amount":amount})
    set_state(cid,"wait_withdraw_account")
    send_msg(cid,"🏦 Enter your *UPI ID / Bank Account Number*:",
        reply_markup=types.ForceReply(selective=True))

@bot.message_handler(func=lambda m: get_state(str(m.chat.id))=="wait_withdraw_account")
def handle_withdraw_acc(msg):
    cid = str(msg.chat.id)
    if _guard_cancel(msg): msg_cancel(msg); return
    temp = get_temp(cid)
    amount = temp.get("withdraw_amount",0)
    u = get_user(cid)
    wd_id = gen_order_id()
    fb.put(f"withdrawals/{wd_id}",{
        "chat_id":cid,"user_name":u.get("full_name",""),"amount":amount,
        "account":msg.text.strip(),"status":"pending","created_at":now_str()
    })
    fb.patch(f"users/{cid}",{"wallet":u.get("wallet",0)-amount})
    fb.put(f"users/{cid}/transactions/{wd_id}",{
        "type":"withdrawal","for":msg.text.strip(),
        "amount":-amount,"status":"pending","date":now_str()
    })
    clear_state(cid)
    send_msg(cid,
        f"✅ *Withdrawal Requested!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Amount: *{fmt_price(amount)}*\n"
        f"🏦 Account: `{msg.text.strip()}`\n"
        f"⏳ Processing: 24–48 hrs",
        reply_markup=kb_main())
    send_notify(f"🏦 *Withdrawal!* {u.get('full_name',cid)} requested {fmt_price(amount)}")

# ── Wallet ────────────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text=="💰 Wallet")
def msg_wallet(msg):
    if not _guard(msg): return
    _show_wallet(str(msg.chat.id))

def _show_wallet(cid, mid=None):
    u = get_user(cid)
    if not u: return
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton("➕ Add Money", callback_data="wallet_deposit"),
           types.InlineKeyboardButton("➖ Withdraw",  callback_data="wallet_withdraw"))
    text = (
        f"💰 *My Wallet*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: *{fmt_price(u.get('wallet',0))}*\n"
        f"📥 Total Deposited: *{fmt_price(u.get('total_deposit',0))}*\n"
        f"🛍️ Total Spent: *{fmt_price(u.get('total_spent',0))}*\n"
        f"💸 Total Earned: *{fmt_price(u.get('total_earned',0))}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    if mid:
        try: bot.edit_message_text(text,cid,mid,parse_mode="Markdown",reply_markup=mk); return
        except: pass
    bot.send_message(cid,text,parse_mode="Markdown",reply_markup=mk)

# ── Transactions ──────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text=="💳 Transactions")
def msg_transactions(msg):
    if not _guard(msg): return
    cid = str(msg.chat.id)
    txns = fb.get(f"users/{cid}/transactions") or {}
    if not txns:
        send_msg(cid,"💳 *Transactions*\n━━━━━━━━━━━━━━━━━━━━━\nNo transactions yet."); return
    lines = ["💳 *Transaction History*\n━━━━━━━━━━━━━━━━━━━━━"]
    icons = {"purchase":"🛍️","deposit":"💵","withdrawal":"🏦","referral":"🤝"}
    status_icons = {"success":"✅","pending":"⏳","failed":"❌"}
    for tid,td in sorted(txns.items(),key=lambda x:x[1].get("date",""),reverse=True)[:15]:
        icon = icons.get(td.get("type",""),"📋")
        st = status_icons.get(td.get("status","pending"),"⏳")
        amt = td.get("amount",0)
        amt_str = f"+{fmt_price(amt)}" if amt>0 else fmt_price(amt)
        lines.append(
            f"{st} {icon} *{td.get('type','').title()}*\n"
            f"   For: {td.get('for','')}\n"
            f"   Amount: *{amt_str}* | {td.get('date','')[:16]}")
    send_msg(cid,"\n\n".join(lines))

# ── Refer & Earn ──────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text in ("👥 Refer & Earn","👤 My Referrals"))
def msg_refer(msg):
    if not _guard(msg): return
    cid = str(msg.chat.id)
    u = get_user(cid)
    if not u: return
    rc = u.get("refer_code","")
    uname = BOT_USERNAME()
    link = f"https://t.me/{uname}?start={rc}"
    comm = REFER_COMMISSION()
    if msg.text == "👤 My Referrals":
        refs = fb.get(f"referrals/{cid}") or {}
        lines = ["👤 *My Referrals*\n━━━━━━━━━━━━━━━━━━━━━"]
        if not refs:
            lines.append("No referrals yet.\n\n📤 Share your link to start earning!")
        else:
            for i,(rid,rd) in enumerate(refs.items(),1):
                st = "✅" if rd.get("status")=="verified" else "⏳"
                lines.append(f"{i}. {st} *{rd.get('name','User')}*\n   💰 Earned: {fmt_price(rd.get('earned',0))}")
        send_msg(cid,"\n".join(lines))
    else:
        send_msg(cid,
            f"👥 *Refer & Earn*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💸 Earn *{comm}% commission* on every purchase by your referrals!\n\n"
            f"🔗 *Your Referral Link:*\n`{link}`\n\n"
            f"📊 *Stats:*\n"
            f"  👥 Total: *{u.get('refer_count',0)}*\n"
            f"  ✅ Verified: *{u.get('verified_refer',0)}*\n"
            f"  ⏳ Pending: *{u.get('pending_refer',0)}*\n"
            f"  💰 Total Earned: *{fmt_price(u.get('total_earned',0))}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📤 Share and start earning!")

# ── Rules ─────────────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text=="📋 Rules")
def msg_rules(msg):
    if not _guard(msg): return
    send_msg(str(msg.chat.id), RULES_TEXT())

# ── Support ───────────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text=="📞 Support")
def msg_support(msg):
    if not _guard(msg): return
    sup = SUPPORT_USERNAME()
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("💬 Contact Support",url=f"https://t.me/{sup.lstrip('@')}"))
    send_msg(str(msg.chat.id),
        f"📞 *Customer Support*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Available: 24/7\n"
        f"📬 Contact: {sup}\n\n"
        f"For:\n• Order issues\n• Payment problems\n• Account help\n"
        f"━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=mk)

# ── Broadcast (called by admin panel) ────────────────────────────────────────
def broadcast_message(text=None, image_url=None, chat_ids=None):
    if not chat_ids:
        users = fb.get("users") or {}
        chat_ids = list(users.keys())
    ok=fail=0
    for cid in chat_ids:
        try:
            if image_url and text:
                bot.send_photo(cid,image_url,caption=text,parse_mode="Markdown")
            elif image_url:
                bot.send_photo(cid,image_url)
            elif text:
                bot.send_message(cid,text,parse_mode="Markdown")
            else:
                continue
            ok+=1; time.sleep(0.04)
        except Exception as e:
            print(f"[Broadcast] {cid}: {e}"); fail+=1
    return ok,fail

def notify_order_status(order_id, new_status):
    """Called by admin when updating order status."""
    order = fb.get(f"orders/{order_id}")
    if not order: return
    cid = order.get("chat_id")
    if not cid: return
    is_cart = bool(order.get("items"))
    product = order.get("product_name") or (f"{len(order.get('items',{}))} item(s)" if is_cart else "Order")
    icons = {"processing":"⚙️","completed":"✅","failed":"❌","pending":"⏳","cancelled":"🚫"}
    icon = icons.get(new_status,"📋")
    send_msg(cid,
        f"{icon} *Order Update!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Order: `{order_id}`\n"
        f"📦 {product}\n"
        f"💰 Amount: {fmt_price(order.get('price',order.get('total',0)))}\n"
        f"📊 New Status: *{new_status.title()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━")
    # Update user transaction status too
    fb.patch(f"users/{cid}/transactions/{order_id}",{"status":new_status})
    # notify admins
    send_notify(f"📋 Order `{order_id}` → *{new_status}* by admin")

def run_bot():
    if not bot:
        print("❌ Bot not started — no BOT_TOKEN in Firebase /config"); return
    print("🤖 Bot polling started")
    bot.infinity_polling(timeout=30,long_polling_timeout=30,restart_on_change=False)
