"""
support_bot.py — HamzaShop Customer Support Bot
Separate bot token stored in Firebase /config/support_bot_token
Messages stored in Firebase /support/{chat_id}/messages/{msg_id}
"""
import telebot
from telebot import types
import datetime, os, time
import firebase_helper as fb

def _get_support_token():
    token = os.environ.get("SUPPORT_BOT_TOKEN","").strip()
    if token: return token
    return (fb.get("config/support_bot_token") or "").strip()

def _make_support_bot():
    token = _get_support_token()
    if not token:
        print("⚠️  SUPPORT_BOT_TOKEN not set"); return None
    return telebot.TeleBot(token, parse_mode=None)

support_bot = _make_support_bot()

def now_str(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def now_ts():  return int(datetime.datetime.now().timestamp())

# ── Helpers ───────────────────────────────────────────────────────────────────
def send_msg(cid, text, **kw):
    try: support_bot.send_message(cid, text, parse_mode="Markdown", **kw)
    except Exception as e: print(f"[SUPPORT MSG] {cid}: {e}")

def store_message(chat_id, msg_id, data: dict):
    fb.put(f"support/{chat_id}/messages/{msg_id}", data)
    # Update conversation meta
    fb.patch(f"support/{chat_id}/meta", {
        "last_message":   data.get("text") or data.get("caption") or "[image]",
        "last_time":      data.get("time", now_str()),
        "last_ts":        now_ts(),
        "unread":         (fb.get(f"support/{chat_id}/meta/unread") or 0) + 1,
        "chat_id":        str(chat_id),
        "user_name":      data.get("user_name",""),
        "username":       data.get("username",""),
    })

def mark_delivered(chat_id, msg_id):
    fb.patch(f"support/{chat_id}/messages/{msg_id}", {"delivered": True})

# ── /start ────────────────────────────────────────────────────────────────────
@support_bot.message_handler(commands=["start"])
def cmd_start(msg):
    cid = str(msg.chat.id)
    fn  = msg.from_user.first_name or "Friend"
    un  = msg.from_user.username or ""
    # Init meta if new
    meta = fb.get(f"support/{cid}/meta") or {}
    if not meta:
        fb.put(f"support/{cid}/meta", {
            "chat_id": cid, "user_name": fn,
            "username": un, "started_at": now_str(),
            "last_message": "", "last_time": now_str(),
            "last_ts": now_ts(), "unread": 0,
        })
    greeting = fb.get("config/support_greeting") or (
        "👋 *Welcome to Customer Support!*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Please describe your issue and we'll get back to you shortly.\n\n"
        "📸 You can also send screenshots or images.\n"
        "⏱️ Response time: Usually within a few hours."
    )
    support_bot.send_message(cid, greeting, parse_mode="Markdown")

# ── User sends text ───────────────────────────────────────────────────────────
@support_bot.message_handler(content_types=["text"])
def handle_text(msg):
    cid    = str(msg.chat.id)
    mid    = str(msg.message_id)
    fn     = msg.from_user.first_name or ""
    un     = msg.from_user.username or ""
    uname  = f"{fn} {msg.from_user.last_name or ''}".strip()

    # Update meta name
    fb.patch(f"support/{cid}/meta", {"user_name": uname, "username": un, "chat_id": cid})

    store_message(cid, mid, {
        "msg_id":    mid,
        "chat_id":   cid,
        "user_name": uname,
        "username":  un,
        "text":      msg.text,
        "type":      "text",
        "from":      "user",
        "time":      now_str(),
        "ts":        now_ts(),
        "read":      False,
        "delivered": True,
        "edited":    False,
    })

    # Auto-ack
    try:
        support_bot.send_chat_action(cid, "typing")
        auto_reply = fb.get("config/support_auto_reply") or ""
        if auto_reply:
            time.sleep(0.8)
            support_bot.send_message(cid, auto_reply, parse_mode="Markdown")
    except: pass

# ── User sends photo ──────────────────────────────────────────────────────────
@support_bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    cid  = str(msg.chat.id)
    mid  = str(msg.message_id)
    fn   = msg.from_user.first_name or ""
    un   = msg.from_user.username or ""
    uname = f"{fn} {msg.from_user.last_name or ''}".strip()

    # Get highest resolution file_id
    photo    = msg.photo[-1]
    file_id  = photo.file_id
    caption  = msg.caption or ""

    # Get public URL via getFile
    try:
        file_info = support_bot.get_file(file_id)
        token     = _get_support_token()
        file_url  = f"https://api.telegram.org/file/bot{token}/{file_info.file_path}"
    except:
        file_url = ""

    fb.patch(f"support/{cid}/meta", {"user_name": uname, "username": un, "chat_id": cid})

    store_message(cid, mid, {
        "msg_id":    mid,
        "chat_id":   cid,
        "user_name": uname,
        "username":  un,
        "text":      caption,
        "caption":   caption,
        "file_id":   file_id,
        "file_url":  file_url,
        "type":      "photo",
        "from":      "user",
        "time":      now_str(),
        "ts":        now_ts(),
        "read":      False,
        "delivered": True,
        "edited":    False,
    })

# ── Admin reply forwarded from panel ─────────────────────────────────────────
def admin_reply(chat_id: str, text: str, image_url: str = "") -> dict:
    """Called by Flask /support/<cid>/send route."""
    if not support_bot:
        return {"error": "Support bot not running"}
    mid = None
    try:
        if image_url and text:
            m = support_bot.send_photo(chat_id, image_url, caption=text, parse_mode="Markdown")
        elif image_url:
            m = support_bot.send_photo(chat_id, image_url)
        else:
            m = support_bot.send_message(chat_id, text, parse_mode="Markdown")
        mid = str(m.message_id)

        # Store admin message in Firebase
        fb.put(f"support/{chat_id}/messages/admin_{mid}", {
            "msg_id":   f"admin_{mid}",
            "chat_id":  chat_id,
            "text":     text,
            "image_url":image_url,
            "type":     "photo" if image_url else "text",
            "from":     "admin",
            "time":     now_str(),
            "ts":       now_ts(),
            "read":     False,
            "delivered":True,
            "edited":   False,
        })
        # Update meta
        fb.patch(f"support/{chat_id}/meta", {
            "last_message": text or "[image]",
            "last_time":    now_str(),
            "last_ts":      now_ts(),
        })
        # Reset unread on admin reply
        fb.patch(f"support/{chat_id}/meta", {"unread": 0})
        return {"ok": True, "mid": mid}
    except Exception as e:
        return {"error": str(e)}

def admin_edit_message(chat_id: str, admin_mid: str, new_text: str) -> dict:
    """Edit an admin message already sent to the user."""
    if not support_bot: return {"error":"Bot not running"}
    try:
        real_mid = int(admin_mid.replace("admin_",""))
        support_bot.edit_message_text(new_text, chat_id, real_mid, parse_mode="Markdown")
        fb.patch(f"support/{chat_id}/messages/{admin_mid}",
                 {"text": new_text, "edited": True, "edited_at": now_str()})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

def admin_delete_message(chat_id: str, admin_mid: str) -> dict:
    """Delete an admin message from Telegram and Firebase."""
    if not support_bot: return {"error":"Bot not running"}
    try:
        real_mid = int(admin_mid.replace("admin_",""))
        support_bot.delete_message(chat_id, real_mid)
    except: pass
    fb.delete(f"support/{chat_id}/messages/{admin_mid}")
    return {"ok": True}

def run_support_bot():
    if not support_bot:
        print("❌ Support bot token missing — not started"); return
    print("🎧 Support bot polling started")
    support_bot.infinity_polling(timeout=30, long_polling_timeout=30)
