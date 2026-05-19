"""
HamzaShop Telegram Bot  –  bot.py
All features: shop, cart, wallet, refer&earn, history, admin broadcast, etc.
"""

import telebot
from telebot import types
import datetime, random, string, time, re
from config import *
import firebase_helper as fb

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# ─────────────────────────────────────────────
# IN-MEMORY STATE  (chat_id → state/temp data)
# ─────────────────────────────────────────────
user_states   = {}   # chat_id: "state_string"
user_temp     = {}   # chat_id: {arbitrary temp dict}

def get_state(cid):   return user_states.get(str(cid))
def set_state(cid, s): user_states[str(cid)] = s
def clear_state(cid): user_states.pop(str(cid), None); user_temp.pop(str(cid), None)
def get_temp(cid):    return user_temp.get(str(cid), {})
def set_temp(cid, d): user_temp[str(cid)] = d
def upd_temp(cid, d): user_temp.setdefault(str(cid), {}).update(d)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def now_str(): return datetime.datetime.now().isoformat()

def gen_code(n=8):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

def gen_order_id():
    return "ORD" + "".join(random.choices(string.digits, k=8))



def get_commission():
    return fb.get_setting("refer_commission", DEFAULT_REFER_COMMISSION)

def fmt_price(p): return f"₹{p:,.0f}"

def paginate(items, page, per_page=5):
    start = page * per_page
    return items[start:start+per_page], len(items) > start + per_page, page > 0

# ─────────────────────────────────────────────
# USER  (create / fetch)
# ─────────────────────────────────────────────
def get_user(cid):
    return fb.get(f"users/{cid}")

def ensure_user(message, referred_by=None):
    cid = str(message.chat.id)
    u = fb.get(f"users/{cid}")
    if u:
        fb.patch(f"users/{cid}", {"last_seen": now_str()})
        return u, False
    # New user
    rc = gen_code()
    while fb.get(f"refer_codes/{rc}"):
        rc = gen_code()
    fn = (message.from_user.first_name or "").strip()
    ln = (message.from_user.last_name  or "").strip()
    data = {
        "chat_id": cid, "full_name": f"{fn} {ln}".strip(),
        "first_name": fn, "last_name": ln,
        "username": message.from_user.username or "",
        "telegram_userid": message.from_user.id,
        "refer_code": rc, "referred_by": referred_by or "",
        "wallet": 0, "total_earned": 0,
        "verified_refer": 0, "pending_refer": 0, "refer_count": 0,
        "total_spent": 0, "total_deposit": 0,
        "purchase_count": 0, "verified": False,
        "created_at": now_str(), "last_seen": now_str(),
    }
    fb.put(f"users/{cid}", data)
    fb.put(f"refer_codes/{rc}", cid)
    if referred_by and referred_by != cid:
        ref = fb.get(f"users/{referred_by}")
        if ref:
            fb.patch(f"users/{referred_by}", {
                "pending_refer": ref.get("pending_refer", 0) + 1,
                "refer_count":   ref.get("refer_count",   0) + 1,
            })
            fb.put(f"referrals/{referred_by}/{cid}", {
                "chat_id": cid, "name": data["full_name"],
                "status": "pending", "joined_at": now_str(), "earned": 0
            })
    return data, True

# ─────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────
def kb_main():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("🏠 Home", "🛒 My Cart")
    m.add("👥 Refer & Earn", "📜 History")
    m.add("👤 My Refer", "💰 Wallet")
    m.add("📋 Rules", "📞 Customer Support")
    return m

def kb_categories():
    m = types.InlineKeyboardMarkup(row_width=2)
    cats = fb.get("categories") or {}
    btns = []
    for cid, cd in cats.items():
        if cd.get("active", True):
            btns.append(types.InlineKeyboardButton(
                f"{cd.get('emoji','🏷️')} {cd['name']}", callback_data=f"cat_{cid}"))
    # Built-in defaults if no categories in DB yet
    defaults = [
        ("💎 FF Diamond", "cat_ff_diamond"),
        ("🎮 PUBG UC",    "cat_pubg_uc"),
        ("📱 Mobile",     "cat_mobile"),
        ("💻 Laptop",     "cat_laptop"),
        ("🎁 Other",      "cat_other"),
    ]
    seen = {b.callback_data for b in btns}
    for name, cbd in defaults:
        if cbd not in seen:
            btns.append(types.InlineKeyboardButton(name, callback_data=cbd))
    for i in range(0, len(btns), 2):
        row = btns[i:i+2]
        m.row(*row)
    m.add(types.InlineKeyboardButton("🏠 Home", callback_data="home"))
    return m

def kb_back(cbd="shop"):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("🔙 Back", callback_data=cbd))
    return m

def kb_product_actions(product_id, category):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("🛒 Add to Cart", callback_data=f"addcart_{product_id}"),
        types.InlineKeyboardButton("⚡ Buy Now",     callback_data=f"buynow_{product_id}"),
    )
    m.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"cat_{category}"))
    return m

# ─────────────────────────────────────────────
# PRODUCT LISTING  (paginated)
# ─────────────────────────────────────────────
def send_products(cid, mid, category, page=0):
    products = fb.get(f"products") or {}
    cat_products = [(pid, pd) for pid, pd in products.items()
                    if pd.get("category") == category and pd.get("active", True)]
    if not cat_products:
        try:
            bot.edit_message_text("❌ No products found in this category.", cid, mid,
                                  reply_markup=kb_back("shop"))
        except: pass
        return

    items, has_next, has_prev = paginate(cat_products, page, per_page=5)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for pid, pd in items:
        label = f"{pd.get('name','Product')} — {fmt_price(pd.get('price',0))}"
        markup.add(types.InlineKeyboardButton(label, callback_data=f"prod_{pid}"))

    nav = []
    if has_prev: nav.append(types.InlineKeyboardButton("⬅️ Prev", callback_data=f"pg_{category}_{page-1}"))
    if has_next: nav.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"pg_{category}_{page+1}"))
    if nav: markup.row(*nav)
    markup.add(types.InlineKeyboardButton("🔙 Categories", callback_data="shop"))

    cat_names = {
        "cat_ff_diamond": "💎 FF Diamond",
        "cat_pubg_uc":    "🎮 PUBG UC",
        "cat_mobile":     "📱 Mobile",
        "cat_laptop":     "💻 Laptop",
        "cat_other":      "🎁 Other",
    }
    title = cat_names.get(category, category)
    text = (f"🏪 *{title}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *{len(cat_products)}* product(s) found\n"
            f"📄 Page {page+1}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 Select a product:")
    try:
        bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=markup)
    except:
        bot.send_message(cid, text, parse_mode="Markdown", reply_markup=markup)

# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    cid  = str(msg.chat.id)
    args = msg.text.split()
    ref_by = None
    if len(args) > 1:
        code = args[1]
        referrer = fb.get(f"refer_codes/{code}")
        if referrer and str(referrer) != cid:
            ref_by = str(referrer)

    u, is_new = ensure_user(msg, ref_by)
    # Mark verified immediately — no channel join required
    if not u.get("verified"):
        fb.patch(f"users/{cid}", {"verified": True})

    if is_new:
        bot.send_message(
            cid,
            f"🎉 *Welcome to HamzaShop!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Hello *{u.get('first_name','Friend')}*! 👋\n\n"
            f"🛍️ *FF Diamond* | *PUBG UC* | *Mobile* | *Laptop*\n"
            f"💰 Wallet: ₹*{u.get('wallet', 0)}*\n"
            f"🤝 Refer & Earn *10%* commission!\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 Use the menu below:",
            parse_mode="Markdown", reply_markup=kb_main())
    else:
        send_home(msg)



# ─────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────
def send_home(msg_or_cid, message_id=None):
    if isinstance(msg_or_cid, str):
        cid = msg_or_cid; fname = "Friend"
    else:
        cid = str(msg_or_cid.chat.id)
        fname = msg_or_cid.from_user.first_name or "Friend"
    u = get_user(cid)
    if not u: return
    fname = u.get("first_name", fname)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("🛍️ Shop Now",    callback_data="shop"),
        types.InlineKeyboardButton("💰 Wallet",      callback_data="wallet"),
    )
    m.add(
        types.InlineKeyboardButton("🛒 My Cart",     callback_data="view_cart"),
        types.InlineKeyboardButton("👥 Refer & Earn",callback_data="refer"),
    )
    text = (
        f"🏠 *Home*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Hello *{fname}*!\n\n"
        f"💰 Wallet: ₹*{u.get('wallet',0):,.2f}*\n"
        f"🛒 Orders: *{u.get('purchase_count',0)}*\n"
        f"🤝 Referrals: *{u.get('refer_count',0)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    if isinstance(msg_or_cid, str) or message_id:
        mid = message_id or (msg_or_cid if isinstance(msg_or_cid, int) else None)
        if mid:
            try:
                bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=m)
                return
            except: pass
    bot.send_message(cid, text, parse_mode="Markdown", reply_markup=m)

@bot.message_handler(func=lambda m: m.text == "🏠 Home")
def msg_home(msg):
    if not _guard(msg): return
    send_home(msg)

@bot.callback_query_handler(func=lambda c: c.data == "home")
def cb_home(c):
    cid = str(c.message.chat.id)
    send_home(cid, c.message.message_id)

# ─────────────────────────────────────────────
# GUARD
# ─────────────────────────────────────────────
def _guard(msg):
    cid = str(msg.chat.id)
    u = fb.get(f"users/{cid}")
    if not u:
        cmd_start(msg); return False
    fb.patch(f"users/{cid}", {"last_seen": now_str()})
    return True

# ─────────────────────────────────────────────
# SHOP / CATEGORIES
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text in ("🛍️ Shop","🛒 Shop Now"))
def msg_shop(msg):
    if not _guard(msg): return
    bot.send_message(str(msg.chat.id),
        "🛍️ *Shop — Categories*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 Choose a category:",
        parse_mode="Markdown", reply_markup=kb_categories())

@bot.callback_query_handler(func=lambda c: c.data == "shop")
def cb_shop(c):
    cid = str(c.message.chat.id)
    try:
        bot.edit_message_text(
            "🛍️ *Shop — Categories*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "👇 Choose a category:",
            cid, c.message.message_id,
            parse_mode="Markdown", reply_markup=kb_categories())
    except:
        bot.send_message(cid,
            "🛍️ *Shop — Categories*",
            parse_mode="Markdown", reply_markup=kb_categories())

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def cb_category(c):
    category = c.data  # e.g. "cat_ff_diamond"
    cid = str(c.message.chat.id)

    # Track views
    fb.patch(f"category_views/{category}", {"views": (fb.get(f"category_views/{category}") or {}).get("views", 0) + 1})

    if category == "cat_mobile":
        # Sub-brands
        brands = fb.get("mobile_brands") or {"samsung": "Samsung 📱", "apple": "Apple 🍎", "vivo": "Vivo 📲"}
        m = types.InlineKeyboardMarkup(row_width=2)
        for bid, bname in brands.items():
            m.add(types.InlineKeyboardButton(bname, callback_data=f"brand_{bid}"))
        m.add(types.InlineKeyboardButton("🔙 Categories", callback_data="shop"))
        try:
            bot.edit_message_text("📱 *Mobile — Choose Brand*\n━━━━━━━━━━━━━━━━━━━━━",
                cid, c.message.message_id, parse_mode="Markdown", reply_markup=m)
        except: pass
        return

    send_products(cid, c.message.message_id, category)

@bot.callback_query_handler(func=lambda c: c.data.startswith("brand_"))
def cb_brand(c):
    brand = c.data[6:]
    cid = str(c.message.chat.id)
    send_products(cid, c.message.message_id, f"cat_mobile_{brand}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("pg_"))
def cb_page(c):
    _, category, page = c.data.split("_", 2)
    send_products(str(c.message.chat.id), c.message.message_id, f"cat_{category}", int(page))

# ─────────────────────────────────────────────
# PRODUCT DETAIL
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("prod_"))
def cb_product(c):
    pid = c.data[5:]
    cid = str(c.message.chat.id)
    p = fb.get(f"products/{pid}")
    if not p:
        bot.answer_callback_query(c.id, "❌ Product not found"); return

    # Track views
    fb.patch(f"products/{pid}", {"views": p.get("views", 0) + 1})

    cat = p.get("category", "cat_other")
    fields = p.get("fields", [])  # custom fields list

    text = (
        f"🏷️ *{p.get('name','Product')}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 {p.get('description','')}\n\n"
        f"💰 Price: *{fmt_price(p.get('price',0))}*\n"
        f"📦 Stock: {'✅ Available' if p.get('stock',1)>0 else '❌ Out of Stock'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    # Add extra info for gaming topups
    if "ff_diamond" in cat or "pubg" in cat:
        text += f"\n🎮 *{p.get('game_info','')}*"

    m = types.InlineKeyboardMarkup(row_width=2)
    if p.get("stock", 1) > 0:
        m.add(
            types.InlineKeyboardButton("🛒 Add to Cart", callback_data=f"addcart_{pid}"),
            types.InlineKeyboardButton("⚡ Buy Now",     callback_data=f"buynow_{pid}"),
        )
    m.add(types.InlineKeyboardButton("🔙 Back", callback_data=cat))

    try:
        if p.get("image_url"):
            bot.send_photo(cid, p["image_url"], caption=text, parse_mode="Markdown", reply_markup=m)
        else:
            bot.edit_message_text(text, cid, c.message.message_id, parse_mode="Markdown", reply_markup=m)
    except:
        bot.send_message(cid, text, parse_mode="Markdown", reply_markup=m)

# ─────────────────────────────────────────────
# ADD TO CART
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("addcart_"))
def cb_add_cart(c):
    pid = c.data[8:]
    cid = str(c.message.chat.id)
    p = fb.get(f"products/{pid}")
    if not p:
        bot.answer_callback_query(c.id, "❌ Product not found"); return
    cart = fb.get(f"carts/{cid}") or {}
    if pid in cart:
        cart[pid]["qty"] = cart[pid].get("qty", 1) + 1
    else:
        cart[pid] = {
            "product_id": pid, "name": p["name"],
            "price": p["price"], "qty": 1,
            "category": p.get("category",""),
            "added_at": now_str()
        }
    fb.put(f"carts/{cid}", cart)
    bot.answer_callback_query(c.id, f"✅ Added to cart: {p['name']}", show_alert=True)

# ─────────────────────────────────────────────
# VIEW CART
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🛒 My Cart")
def msg_cart(msg):
    if not _guard(msg): return
    _show_cart(str(msg.chat.id))

@bot.callback_query_handler(func=lambda c: c.data == "view_cart")
def cb_view_cart(c):
    _show_cart(str(c.message.chat.id), c.message.message_id)

def _show_cart(cid, mid=None):
    cart = fb.get(f"carts/{cid}") or {}
    if not cart:
        text = ("🛒 *My Cart*\n━━━━━━━━━━━━━━━━━━━━━\n"
                "Your cart is empty.\n👉 Go to Shop to add items!")
        m = types.InlineKeyboardMarkup()
        m.add(types.InlineKeyboardButton("🛍️ Shop Now", callback_data="shop"))
        if mid:
            try: bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=m); return
            except: pass
        bot.send_message(cid, text, parse_mode="Markdown", reply_markup=m)
        return

    total = sum(v["price"]*v.get("qty",1) for v in cart.values())
    lines = ["🛒 *My Cart*\n━━━━━━━━━━━━━━━━━━━━━"]
    for i,(pid,item) in enumerate(cart.items(),1):
        lines.append(f"{i}. *{item['name']}*\n   Qty:{item.get('qty',1)} × {fmt_price(item['price'])} = {fmt_price(item['price']*item.get('qty',1))}")
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━\n💰 *Total: {fmt_price(total)}*")

    m = types.InlineKeyboardMarkup(row_width=2)
    # Remove buttons per item
    for pid, item in cart.items():
        m.add(types.InlineKeyboardButton(f"❌ Remove {item['name'][:15]}", callback_data=f"rmcart_{pid}"))
    m.add(
        types.InlineKeyboardButton("🗑️ Clear Cart",  callback_data="clear_cart"),
        types.InlineKeyboardButton("💳 Checkout",    callback_data="checkout_cart"),
    )
    m.add(types.InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop"))

    text = "\n".join(lines)
    if mid:
        try: bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=m); return
        except: pass
    bot.send_message(cid, text, parse_mode="Markdown", reply_markup=m)

@bot.callback_query_handler(func=lambda c: c.data.startswith("rmcart_"))
def cb_rm_cart(c):
    pid = c.data[7:]; cid = str(c.message.chat.id)
    cart = fb.get(f"carts/{cid}") or {}
    name = cart.get(pid,{}).get("name","item")
    cart.pop(pid, None)
    fb.put(f"carts/{cid}", cart)
    bot.answer_callback_query(c.id, f"🗑️ Removed {name}")
    _show_cart(cid, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == "clear_cart")
def cb_clear_cart(c):
    cid = str(c.message.chat.id)
    fb.delete(f"carts/{cid}")
    bot.answer_callback_query(c.id, "🗑️ Cart cleared!")
    _show_cart(cid, c.message.message_id)

# ─────────────────────────────────────────────
# BUY NOW FLOW
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("buynow_"))
def cb_buy_now(c):
    pid = c.data[7:]; cid = str(c.message.chat.id)
    p = fb.get(f"products/{pid}")
    if not p:
        bot.answer_callback_query(c.id, "❌ Not found"); return

    cat = p.get("category","")
    set_temp(cid, {"product_id": pid, "product": p, "category": cat})

    # Determine required fields
    if "ff_diamond" in cat:
        _ask_ff_uid(cid, p)
    elif "pubg" in cat:
        _ask_pubg_uid(cid, p)
    elif "mobile" in cat or "laptop" in cat or "other" in cat:
        _ask_delivery_info(cid, p)
    else:
        _ask_delivery_info(cid, p)

def _ask_ff_uid(cid, p):
    set_state(cid, "wait_ff_uid")
    bot.send_message(cid,
        f"💎 *Free Fire Diamond*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Product: *{p['name']}*\n"
        f"Price: *{fmt_price(p['price'])}*\n\n"
        f"📝 Please enter your *Free Fire UID*:",
        parse_mode="Markdown",
        reply_markup=types.ForceReply(selective=True))

def _ask_pubg_uid(cid, p):
    set_state(cid, "wait_pubg_uid")
    bot.send_message(cid,
        f"🎮 *PUBG UC Top-up*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Product: *{p['name']}*\n"
        f"Price: *{fmt_price(p['price'])}*\n\n"
        f"📝 Please enter your *PUBG UID*:",
        parse_mode="Markdown",
        reply_markup=types.ForceReply(selective=True))

def _ask_delivery_info(cid, p):
    set_state(cid, "wait_delivery_name")
    upd_temp(cid, {"delivery": {}})
    bot.send_message(cid,
        f"🏠 *Delivery Details Required*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Product: *{p['name']}*\n"
        f"Price: *{fmt_price(p['price'])}*\n\n"
        f"📝 Enter your *Full Name*:",
        parse_mode="Markdown",
        reply_markup=types.ForceReply(selective=True))

# ─────────────────────────────────────────────
# STATE-DRIVEN MESSAGE HANDLER
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: get_state(str(m.chat.id)) is not None)
def handle_states(msg):
    cid = str(msg.chat.id)
    state = get_state(cid)
    txt = msg.text.strip() if msg.text else ""

    # ── FF / PUBG UID ──
    if state in ("wait_ff_uid","wait_pubg_uid"):
        uid = txt
        if not uid.isdigit() or len(uid) < 6:
            bot.send_message(cid, "❌ Invalid UID. Please enter a valid numeric UID:", reply_markup=types.ForceReply(selective=True)); return
        upd_temp(cid, {"uid": uid})
        set_state(cid, "wait_utr")
        p = get_temp(cid).get("product",{})
        game = "Free Fire" if state == "wait_ff_uid" else "PUBG"
        m = types.InlineKeyboardMarkup()
        m.add(types.InlineKeyboardButton("💳 Pay Now", url=PAYMENT_LINK))
        bot.send_message(cid,
            f"✅ UID confirmed: *{uid}*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎮 Game: *{game}*\n"
            f"📦 Product: *{p.get('name','')}*\n"
            f"💰 Amount: *{fmt_price(p.get('price',0))}*\n\n"
            f"1️⃣ Click *Pay Now* to complete payment\n"
            f"2️⃣ Note your *UTR / Transaction Number*\n"
            f"3️⃣ Send UTR number here to confirm\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Enter your *UTR Number* after paying:",
            parse_mode="Markdown", reply_markup=m)
        return

    # ── UTR ──
    if state == "wait_utr":
        utr = txt
        if len(utr) < 6:
            bot.send_message(cid, "❌ Invalid UTR. Please enter a valid UTR / transaction number:", reply_markup=types.ForceReply(selective=True)); return
        upd_temp(cid, {"utr": utr})
        set_state(cid, "confirm_order")
        temp = get_temp(cid)
        p = temp.get("product",{})
        m = types.InlineKeyboardMarkup(row_width=2)
        m.add(
            types.InlineKeyboardButton("✅ Confirm Order", callback_data="confirm_order"),
            types.InlineKeyboardButton("❌ Cancel",        callback_data="cancel_order"),
        )
        bot.send_message(cid,
            f"🧾 *Order Summary*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Product: *{p.get('name','')}*\n"
            f"💰 Price: *{fmt_price(p.get('price',0))}*\n"
            f"🎮 UID: *{temp.get('uid','N/A')}*\n"
            f"🔖 UTR: *{utr}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Confirm your order?",
            parse_mode="Markdown", reply_markup=m)
        return

    # ── DELIVERY FIELDS ──
    delivery_states = {
        "wait_delivery_name":    ("wait_delivery_mobile", "name",    "📱 Enter your *Mobile Number*:"),
        "wait_delivery_mobile":  ("wait_delivery_pincode","mobile",  "📮 Enter your *PIN Code*:"),
        "wait_delivery_pincode": ("wait_delivery_state",  "pincode", "🏙️ Enter your *State*:"),
        "wait_delivery_state":   ("wait_delivery_country","state",   "🌍 Enter your *Country*:"),
        "wait_delivery_country": ("wait_delivery_address","country", "🏠 Enter your full *Address*:"),
        "wait_delivery_address": (None,                  "address",  None),
    }
    if state in delivery_states:
        next_state, field, next_prompt = delivery_states[state]
        # Validation
        if field == "mobile" and not re.match(r"^\d{7,15}$", txt):
            bot.send_message(cid, "❌ Invalid mobile number. Digits only (7–15):", reply_markup=types.ForceReply(selective=True)); return
        if field == "pincode" and not re.match(r"^\d{4,10}$", txt):
            bot.send_message(cid, "❌ Invalid PIN code:", reply_markup=types.ForceReply(selective=True)); return
        temp = get_temp(cid)
        temp.setdefault("delivery",{})[field] = txt
        set_temp(cid, temp)
        if next_state:
            set_state(cid, next_state)
            bot.send_message(cid, next_prompt, parse_mode="Markdown", reply_markup=types.ForceReply(selective=True))
        else:
            # All delivery info collected → ask payment
            set_state(cid, "wait_utr")
            p = temp.get("product",{})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("💳 Pay Now", url=PAYMENT_LINK))
            bot.send_message(cid,
                f"📦 *Delivery Summary*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Name: {temp['delivery']['name']}\n"
                f"📱 Mobile: {temp['delivery']['mobile']}\n"
                f"📮 PIN: {temp['delivery']['pincode']}\n"
                f"🏙️ State: {temp['delivery']['state']}\n"
                f"🌍 Country: {temp['delivery']['country']}\n"
                f"🏠 Address: {temp['delivery']['address']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Amount to pay: *{fmt_price(p.get('price',0))}*\n\n"
                f"1️⃣ Click *Pay Now*\n"
                f"2️⃣ Send your *UTR/Transaction Number* here:",
                parse_mode="Markdown", reply_markup=m)
        return

    # ── WALLET OPERATIONS ──
    if state == "wait_deposit_amount":
        if not txt.isdigit() or int(txt) < 1:
            bot.send_message(cid, "❌ Enter a valid amount:", reply_markup=types.ForceReply(selective=True)); return
        amount = int(txt)
        upd_temp(cid, {"deposit_amount": amount})
        set_state(cid, "wait_deposit_utr")
        m = types.InlineKeyboardMarkup()
        m.add(types.InlineKeyboardButton("💳 Pay Now", url=PAYMENT_LINK))
        bot.send_message(cid,
            f"💳 *Deposit ₹{amount}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"1️⃣ Click *Pay Now*\n"
            f"2️⃣ Send UTR number after payment:",
            parse_mode="Markdown", reply_markup=m)
        return

    if state == "wait_deposit_utr":
        utr = txt
        if len(utr) < 6:
            bot.send_message(cid, "❌ Invalid UTR:", reply_markup=types.ForceReply(selective=True)); return
        amount = get_temp(cid).get("deposit_amount", 0)
        txn = fb.post("transactions", {
            "chat_id": cid, "type": "deposit", "amount": amount,
            "utr": utr, "status": "pending", "created_at": now_str()
        })
        clear_state(cid)
        bot.send_message(cid,
            f"✅ *Deposit Request Submitted!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Amount: *₹{amount}*\n"
            f"🔖 UTR: *{utr}*\n"
            f"⏳ Status: *Pending Verification*\n\n"
            f"Your wallet will be credited after verification.",
            parse_mode="Markdown", reply_markup=kb_main())
        return

    if state == "wait_withdraw_amount":
        if not txt.isdigit():
            bot.send_message(cid, "❌ Enter a valid amount:", reply_markup=types.ForceReply(selective=True)); return
        amount = int(txt)
        u = get_user(cid)
        wallet = u.get("wallet",0)
        if amount < MIN_WITHDRAWAL:
            bot.send_message(cid, f"❌ Minimum withdrawal is ₹{MIN_WITHDRAWAL}:", reply_markup=types.ForceReply(selective=True)); return
        if amount > wallet:
            bot.send_message(cid, f"❌ Insufficient balance. Your wallet: ₹{wallet}:", reply_markup=types.ForceReply(selective=True)); return
        upd_temp(cid, {"withdraw_amount": amount})
        set_state(cid, "wait_withdraw_account")
        bot.send_message(cid, "🏦 Enter your *UPI ID / Bank Account Number*:", parse_mode="Markdown", reply_markup=types.ForceReply(selective=True))
        return

    if state == "wait_withdraw_account":
        account = txt
        temp = get_temp(cid)
        amount = temp.get("withdraw_amount", 0)
        fb.post("withdrawals", {
            "chat_id": cid, "amount": amount, "account": account,
            "status": "pending", "created_at": now_str(),
            "note": ""
        })
        u = get_user(cid)
        fb.patch(f"users/{cid}", {"wallet": u.get("wallet",0) - amount})
        clear_state(cid)
        bot.send_message(cid,
            f"✅ *Withdrawal Request Submitted!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Amount: *₹{amount}*\n"
            f"🏦 Account: *{account}*\n"
            f"⏳ Status: *Pending* (24–48 hrs)\n",
            parse_mode="Markdown", reply_markup=kb_main())
        return

    if state == "wait_wallet_pay_utr":
        utr = txt
        temp = get_temp(cid)
        amount = temp.get("wallet_pay_amount",0)
        order_id = temp.get("order_id","")
        fb.patch(f"orders/{order_id}", {"utr": utr, "payment_status": "pending", "updated_at": now_str()})
        u = get_user(cid)
        fb.patch(f"users/{cid}", {"wallet": u.get("wallet",0) - amount})
        clear_state(cid)
        bot.send_message(cid,
            f"✅ *Order Placed!*\n"
            f"🔖 Order ID: *{order_id}*\n"
            f"💰 Amount: *₹{amount}*\n"
            f"⏳ Status: *Pending Verification*",
            parse_mode="Markdown", reply_markup=kb_main())
        return

# ─────────────────────────────────────────────
# CONFIRM / CANCEL ORDER
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "confirm_order")
def cb_confirm_order(c):
    cid = str(c.message.chat.id)
    temp = get_temp(cid)
    p = temp.get("product", {})
    uid = temp.get("uid","")
    utr = temp.get("utr","")
    delivery = temp.get("delivery",{})
    order_id = gen_order_id()
    u = get_user(cid)

    order = {
        "order_id": order_id, "chat_id": cid,
        "user_name": u.get("full_name",""),
        "product_id": temp.get("product_id",""),
        "product_name": p.get("name",""),
        "price": p.get("price",0),
        "category": temp.get("category",""),
        "uid": uid, "delivery": delivery,
        "utr": utr, "payment_status": "pending",
        "order_status": "pending",
        "created_at": now_str(), "updated_at": now_str(),
    }
    fb.put(f"orders/{order_id}", order)

    # Update user stats
    fb.patch(f"users/{cid}", {
        "purchase_count": u.get("purchase_count",0)+1,
        "total_spent": u.get("total_spent",0)+p.get("price",0),
    })

    # Add to purchase history
    fb.put(f"users/{cid}/purchase_history/{order_id}", {
        "order_id": order_id, "product": p.get("name",""),
        "price": p.get("price",0), "status":"pending", "date": now_str()
    })

    # Refer commission (pending until payment verified)
    referred_by = u.get("referred_by","")
    if referred_by:
        commission = get_commission()
        earned = round(p.get("price",0) * commission / 100, 2)
        fb.put(f"referrals/{referred_by}/{cid}/pending_commission", earned)

    clear_state(cid)
    try: bot.delete_message(cid, c.message.message_id)
    except: pass
    bot.send_message(cid,
        f"🎉 *Order Placed Successfully!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔖 Order ID: *{order_id}*\n"
        f"📦 Product: *{p.get('name','')}*\n"
        f"💰 Amount: *{fmt_price(p.get('price',0))}*\n"
        f"⏳ Status: *Pending Payment Verification*\n\n"
        f"We'll notify you once verified! ✉️",
        parse_mode="Markdown", reply_markup=kb_main())

@bot.callback_query_handler(func=lambda c: c.data == "cancel_order")
def cb_cancel_order(c):
    cid = str(c.message.chat.id)
    clear_state(cid)
    bot.answer_callback_query(c.id, "❌ Order cancelled")
    try: bot.delete_message(cid, c.message.message_id)
    except: pass
    bot.send_message(cid, "❌ Order cancelled.", reply_markup=kb_main())

# ─────────────────────────────────────────────
# CHECKOUT CART
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "checkout_cart")
def cb_checkout_cart(c):
    cid = str(c.message.chat.id)
    cart = fb.get(f"carts/{cid}") or {}
    if not cart:
        bot.answer_callback_query(c.id, "🛒 Cart is empty!"); return
    total = sum(v["price"]*v.get("qty",1) for v in cart.values())
    u = get_user(cid)

    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("💳 Pay Online", callback_data="cart_pay_online"),
        types.InlineKeyboardButton("👛 Pay from Wallet", callback_data=f"cart_pay_wallet_{total}"),
    )
    m.add(types.InlineKeyboardButton("🔙 Back to Cart", callback_data="view_cart"))

    bot.edit_message_text(
        f"💳 *Checkout*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛒 Items: *{len(cart)}*\n"
        f"💰 Total: *{fmt_price(total)}*\n"
        f"👛 Wallet Balance: *{fmt_price(u.get('wallet',0))}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose payment method:",
        cid, c.message.message_id,
        parse_mode="Markdown", reply_markup=m)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cart_pay_wallet_"))
def cb_cart_wallet(c):
    cid = str(c.message.chat.id)
    total = float(c.data.split("_")[-1])
    u = get_user(cid)
    if u.get("wallet",0) < total:
        bot.answer_callback_query(c.id, f"❌ Insufficient wallet balance! You have ₹{u.get('wallet',0)}", show_alert=True); return
    cart = fb.get(f"carts/{cid}") or {}
    order_id = gen_order_id()
    order = {
        "order_id": order_id, "chat_id": cid,
        "user_name": u.get("full_name",""),
        "items": cart, "total": total,
        "payment_method": "wallet",
        "payment_status": "success",
        "order_status": "processing",
        "created_at": now_str(), "updated_at": now_str(),
    }
    fb.put(f"orders/{order_id}", order)
    fb.patch(f"users/{cid}", {
        "wallet": u.get("wallet",0) - total,
        "purchase_count": u.get("purchase_count",0) + 1,
        "total_spent": u.get("total_spent",0) + total,
    })
    fb.delete(f"carts/{cid}")
    bot.answer_callback_query(c.id)
    try: bot.delete_message(cid, c.message.message_id)
    except: pass
    bot.send_message(cid,
        f"✅ *Order Placed via Wallet!*\n"
        f"🔖 Order ID: *{order_id}*\n"
        f"💰 Paid: *{fmt_price(total)}*\n"
        f"📦 Status: *Processing*",
        parse_mode="Markdown", reply_markup=kb_main())

@bot.callback_query_handler(func=lambda c: c.data == "cart_pay_online")
def cb_cart_online(c):
    cid = str(c.message.chat.id)
    set_state(cid, "wait_utr")
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("💳 Pay Now", url=PAYMENT_LINK))
    try: bot.delete_message(cid, c.message.message_id)
    except: pass
    bot.send_message(cid,
        "💳 *Pay Online*\n━━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ Click *Pay Now*\n"
        "2️⃣ Enter UTR after payment:",
        parse_mode="Markdown", reply_markup=m)

# ─────────────────────────────────────────────
# REFER & EARN
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "👥 Refer & Earn")
def msg_refer(msg):
    if not _guard(msg): return
    _show_refer(str(msg.chat.id))

@bot.callback_query_handler(func=lambda c: c.data == "refer")
def cb_refer(c):
    _show_refer(str(c.message.chat.id))

def _show_refer(cid):
    u = get_user(cid)
    if not u: return
    commission = get_commission()
    rc = u.get("refer_code","")
    link = f"https://t.me/{BOT_USERNAME}?start={rc}"
    bot.send_message(cid,
        f"👥 *Refer & Earn*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💸 Earn *{commission}% commission* on every purchase made by your referrals!\n\n"
        f"🔗 *Your Refer Link:*\n`{link}`\n\n"
        f"📊 *Your Stats:*\n"
        f"  👥 Total Referrals: *{u.get('refer_count',0)}*\n"
        f"  ✅ Verified: *{u.get('verified_refer',0)}*\n"
        f"  ⏳ Pending: *{u.get('pending_refer',0)}*\n"
        f"  💰 Total Earned: *₹{u.get('total_earned',0):,.2f}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 Share your link and start earning!",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👤 My Refer")
def msg_my_refer(msg):
    if not _guard(msg): return
    cid = str(msg.chat.id)
    refs = fb.get(f"referrals/{cid}") or {}
    if not refs:
        bot.send_message(cid, "👤 *My Referrals*\n━━━━━━━━━━━━━━━━━━━━━\nNo referrals yet.\nShare your link to earn!", parse_mode="Markdown"); return
    lines = ["👤 *My Referrals*\n━━━━━━━━━━━━━━━━━━━━━"]
    for i,(rid,rd) in enumerate(refs.items(),1):
        status = "✅" if rd.get("status")=="verified" else "⏳"
        earned = rd.get("earned",0)
        lines.append(f"{i}. {status} *{rd.get('name','User')}*\n   💰 Earned: ₹{earned}")
    bot.send_message(cid, "\n".join(lines), parse_mode="Markdown")

# ─────────────────────────────────────────────
# WALLET
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "💰 Wallet")
def msg_wallet(msg):
    if not _guard(msg): return
    _show_wallet(str(msg.chat.id))

@bot.callback_query_handler(func=lambda c: c.data in ("wallet","wallet_home"))
def cb_wallet(c):
    _show_wallet(str(c.message.chat.id), c.message.message_id)

def _show_wallet(cid, mid=None):
    u = get_user(cid)
    if not u: return
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("➕ Add Money",  callback_data="wallet_deposit"),
        types.InlineKeyboardButton("➖ Withdraw",   callback_data="wallet_withdraw"),
    )
    m.add(types.InlineKeyboardButton("📜 Transaction History", callback_data="txn_history"))
    text = (
        f"💰 *My Wallet*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: *₹{u.get('wallet',0):,.2f}*\n"
        f"📥 Total Deposited: *₹{u.get('total_deposit',0):,.2f}*\n"
        f"🛍️ Total Spent: *₹{u.get('total_spent',0):,.2f}*\n"
        f"💸 Total Earned: *₹{u.get('total_earned',0):,.2f}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    if mid:
        try: bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=m); return
        except: pass
    bot.send_message(cid, text, parse_mode="Markdown", reply_markup=m)

@bot.callback_query_handler(func=lambda c: c.data == "wallet_deposit")
def cb_deposit(c):
    cid = str(c.message.chat.id)
    set_state(cid, "wait_deposit_amount")
    try: bot.delete_message(cid, c.message.message_id)
    except: pass
    bot.send_message(cid, "💵 *Add Money to Wallet*\n━━━━━━━━━━━━━━━━━━━━━\nEnter amount to deposit (₹):", parse_mode="Markdown", reply_markup=types.ForceReply(selective=True))

@bot.callback_query_handler(func=lambda c: c.data == "wallet_withdraw")
def cb_withdraw(c):
    cid = str(c.message.chat.id)
    set_state(cid, "wait_withdraw_amount")
    try: bot.delete_message(cid, c.message.message_id)
    except: pass
    bot.send_message(cid, f"🏦 *Withdraw from Wallet*\n━━━━━━━━━━━━━━━━━━━━━\nMinimum: ₹{MIN_WITHDRAWAL}\n\nEnter withdrawal amount:", parse_mode="Markdown", reply_markup=types.ForceReply(selective=True))

@bot.callback_query_handler(func=lambda c: c.data == "txn_history")
def cb_txn_history(c):
    cid = str(c.message.chat.id)
    txns = fb.get("transactions") or {}
    my = [(tid,td) for tid,td in txns.items() if td.get("chat_id")==cid]
    if not my:
        bot.answer_callback_query(c.id, "No transactions yet"); return
    lines = ["📜 *Transaction History*\n━━━━━━━━━━━━━━━━━━━━━"]
    for tid, td in sorted(my, key=lambda x: x[1].get("created_at",""), reverse=True)[:10]:
        st = "✅" if td.get("status")=="success" else ("❌" if td.get("status")=="failed" else "⏳")
        lines.append(f"{st} {td.get('type','').upper()} ₹{td.get('amount',0)} — {td.get('created_at','')[:10]}")
    bot.send_message(cid, "\n".join(lines), parse_mode="Markdown")

# ─────────────────────────────────────────────
# HISTORY
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "📜 History")
def msg_history(msg):
    if not _guard(msg): return
    cid = str(msg.chat.id)
    hist = fb.get(f"users/{cid}/purchase_history") or {}
    if not hist:
        bot.send_message(cid, "📜 *Purchase History*\n━━━━━━━━━━━━━━━━━━━━━\nNo purchases yet!", parse_mode="Markdown"); return
    lines = ["📜 *Purchase History*\n━━━━━━━━━━━━━━━━━━━━━"]
    for oid, od in sorted(hist.items(), key=lambda x: x[1].get("date",""), reverse=True)[:10]:
        st = {"success":"✅","pending":"⏳","failed":"❌"}.get(od.get("status","pending"),"⏳")
        lines.append(f"{st} *{od.get('product','')}*\n   ₹{od.get('price',0)} | {od.get('date','')[:10]} | ID: `{oid}`")
    bot.send_message(cid, "\n".join(lines), parse_mode="Markdown")

# ─────────────────────────────────────────────
# RULES
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "📋 Rules")
def msg_rules(msg):
    if not _guard(msg): return
    bot.send_message(str(msg.chat.id), RULES_TEXT, parse_mode="Markdown")

# ─────────────────────────────────────────────
# CUSTOMER SUPPORT
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "📞 Customer Support")
def msg_support(msg):
    if not _guard(msg): return
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("💬 Contact Support", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"))
    bot.send_message(str(msg.chat.id),
        f"📞 *Customer Support*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Available: 24/7\n"
        f"📬 Contact: {SUPPORT_USERNAME}\n\n"
        f"For queries:\n"
        f"• Order issues\n• Payment problems\n• Account help\n"
        f"━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown", reply_markup=m)

# ─────────────────────────────────────────────
# ADMIN BROADCAST (from Flask admin panel)
# ─────────────────────────────────────────────
def broadcast_message(text=None, image_url=None, chat_ids=None):
    """Called by admin panel to send messages to all/selected users."""
    if not chat_ids:
        users = fb.get("users") or {}
        chat_ids = list(users.keys())
    ok, fail = 0, 0
    for cid in chat_ids:
        try:
            if image_url and text:
                bot.send_photo(cid, image_url, caption=text, parse_mode="Markdown")
            elif image_url:
                bot.send_photo(cid, image_url)
            elif text:
                bot.send_message(cid, text, parse_mode="Markdown")
            else:
                continue  # nothing to send
            ok += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"[Broadcast] Failed {cid}: {e}")
            fail += 1
    return ok, fail

def run_bot():
    print("🤖 Bot starting...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
