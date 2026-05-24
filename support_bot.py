"""
support_bot.py — HamzaShop Customer Support Bot
Separate bot token stored in Firebase /config/support_bot_token
Messages stored in Firebase /support/{chat_id}/messages/{msg_id}
"""
import telebot
from telebot import types
import datetime, os, time, io
import firebase_helper as fb
from config import SUPPORT_NOTIFY_CHAT_IDS

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

def send_support_notify(chat_id: str, user_name: str, text: str, msg_type: str = "text"):
    """Notify admin chat IDs about a new support message."""
    try:
        ids = SUPPORT_NOTIFY_CHAT_IDS()
        if not ids or not support_bot:
            return
        # Build panel URL from Firebase config
        panel_url = fb.get("config/panel_url") or ""
        chat_link = f"{panel_url.rstrip('/')}/support/{chat_id}" if panel_url else ""

        icon = {"photo": "🖼️", "video": "🎬", "document": "📄"}.get(msg_type, "💬")
        preview = text[:120] + ("…" if len(text) > 120 else "") if text else f"[{msg_type}]"

        notify_text = (
            f"📩 *New Support Message*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *User:* {user_name}\n"
            f"🆔 *ID:* `{chat_id}`\n"
            f"{icon} *Message:* {preview}\n"
            f"🕐 *Time:* {now_str()}"
        )

        mk = None
        if chat_link:
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("🖥️ Open Admin Panel", url=chat_link))

        for admin_id in ids:
            try:
                support_bot.send_message(
                    admin_id, notify_text,
                    parse_mode="Markdown",
                    reply_markup=mk
                )
            except Exception as e:
                print(f"[NOTIFY] {admin_id}: {e}")
    except Exception as e:
        print(f"[NOTIFY] {e}")

def mark_admin_msgs_read(chat_id: str):
    """Mark all unread admin messages as read (user replied = they saw them)."""
    try:
        msgs = fb.get(f"support/{chat_id}/messages") or {}
        for mid, m in msgs.items():
            if m.get("from") == "admin" and not m.get("read"):
                fb.patch(f"support/{chat_id}/messages/{mid}", {"read": True})
    except Exception as e:
        print(f"[MARK_READ] {e}")

# ── Helpers ───────────────────────────────────────────────────────────────────
def send_msg(cid, text, **kw):
    try: support_bot.send_message(cid, text, parse_mode="Markdown", **kw)
    except Exception as e: print(f"[SUPPORT MSG] {cid}: {e}")

def store_message(chat_id, msg_id, data: dict):
    fb.put(f"support/{chat_id}/messages/{msg_id}", data)
    fb.patch(f"support/{chat_id}/meta", {
        "last_message":   data.get("text") or data.get("caption") or "[media]",
        "last_time":      data.get("time", now_str()),
        "last_ts":        now_ts(),
        "unread":         (fb.get(f"support/{chat_id}/meta/unread") or 0) + 1,
        "chat_id":        str(chat_id),
        "user_name":      data.get("user_name",""),
        "username":       data.get("username",""),
    })

def get_user_photo_url(user_id):
    """Fetch the user's Telegram profile photo and return a public URL."""
    try:
        photos = support_bot.get_user_profile_photos(user_id, limit=1)
        if photos and photos.photos:
            file_id   = photos.photos[0][-1].file_id
            file_info = support_bot.get_file(file_id)
            token     = _get_support_token()
            return f"https://api.telegram.org/file/bot{token}/{file_info.file_path}"
    except Exception as e:
        print(f"[PHOTO] {e}")
    return ""

def _get_file_url(file_id):
    try:
        file_info = support_bot.get_file(file_id)
        token     = _get_support_token()
        return f"https://api.telegram.org/file/bot{token}/{file_info.file_path}"
    except:
        return ""

def _is_blocked(cid):
    return bool(fb.get(f"support/{cid}/meta/blocked"))

BLOCKED_MSG = "🚫 You have been blocked from contacting support."

# ── /start ────────────────────────────────────────────────────────────────────
@support_bot.message_handler(commands=["start"])
def cmd_start(msg):
    cid  = str(msg.chat.id)
    fn   = msg.from_user.first_name or "Friend"
    un   = msg.from_user.username or ""
    bio  = getattr(msg.from_user, 'bio', None) or ""
    uname = f"{fn} {msg.from_user.last_name or ''}".strip()

    if _is_blocked(cid):
        send_msg(cid, BLOCKED_MSG); return

    photo_url = get_user_photo_url(msg.from_user.id)

    meta = fb.get(f"support/{cid}/meta") or {}
    fb.patch(f"support/{cid}/meta", {
        "chat_id":    cid,
        "user_name":  uname,
        "username":   un,
        "photo_url":  photo_url,
        "bio":        bio,
        "started_at": meta.get("started_at", now_str()),
        "last_message": meta.get("last_message",""),
        "last_time":  now_str(),
        "last_ts":    now_ts(),
        "unread":     meta.get("unread", 0),
        "blocked":    False,
    })

    greeting = fb.get("config/support_greeting") or (
        "👋 *Welcome to Customer Support!*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Please describe your issue and we'll get back to you shortly.\n\n"
        "📸 You can also send screenshots, videos or files.\n"
        "⏱️ Response time: Usually within a few hours."
    )
    support_bot.send_message(cid, greeting, parse_mode="Markdown")

# ── User sends text ───────────────────────────────────────────────────────────
@support_bot.message_handler(content_types=["text"])
def handle_text(msg):
    cid   = str(msg.chat.id)
    mid   = str(msg.message_id)
    fn    = msg.from_user.first_name or ""
    un    = msg.from_user.username or ""
    uname = f"{fn} {msg.from_user.last_name or ''}".strip()

    if _is_blocked(cid):
        send_msg(cid, BLOCKED_MSG); return

    # Refresh profile photo silently
    photo_url = fb.get(f"support/{cid}/meta/photo_url") or get_user_photo_url(msg.from_user.id)
    fb.patch(f"support/{cid}/meta", {
        "user_name": uname, "username": un,
        "chat_id": cid, "photo_url": photo_url,
    })

    store_message(cid, mid, {
        "msg_id":    mid, "chat_id": cid,
        "user_name": uname, "username": un,
        "text":      msg.text, "type": "text",
        "from":      "user", "time": now_str(),
        "ts":        now_ts(), "read": False,
        "delivered": True, "edited": False,
    })

    # Notify admins + mark their previous messages as read
    mark_admin_msgs_read(cid)
    send_support_notify(cid, uname, msg.text, "text")

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
    cid   = str(msg.chat.id)
    mid   = str(msg.message_id)
    fn    = msg.from_user.first_name or ""
    un    = msg.from_user.username or ""
    uname = f"{fn} {msg.from_user.last_name or ''}".strip()

    if _is_blocked(cid):
        send_msg(cid, BLOCKED_MSG); return

    photo    = msg.photo[-1]
    file_id  = photo.file_id
    caption  = msg.caption or ""
    file_url = _get_file_url(file_id)

    photo_url = fb.get(f"support/{cid}/meta/photo_url") or get_user_photo_url(msg.from_user.id)
    fb.patch(f"support/{cid}/meta", {
        "user_name": uname, "username": un,
        "chat_id": cid, "photo_url": photo_url,
    })

    store_message(cid, mid, {
        "msg_id":    mid, "chat_id": cid,
        "user_name": uname, "username": un,
        "text":      caption, "caption": caption,
        "file_id":   file_id, "file_url": file_url,
        "type":      "photo", "from": "user",
        "time":      now_str(), "ts": now_ts(),
        "read":      False, "delivered": True, "edited": False,
    })

    mark_admin_msgs_read(cid)
    send_support_notify(cid, uname, caption or "[photo]", "photo")

# ── User sends video ──────────────────────────────────────────────────────────
@support_bot.message_handler(content_types=["video"])
def handle_video(msg):
    cid   = str(msg.chat.id)
    mid   = str(msg.message_id)
    fn    = msg.from_user.first_name or ""
    un    = msg.from_user.username or ""
    uname = f"{fn} {msg.from_user.last_name or ''}".strip()

    if _is_blocked(cid):
        send_msg(cid, BLOCKED_MSG); return

    file_id  = msg.video.file_id
    caption  = msg.caption or ""
    file_url = _get_file_url(file_id)

    photo_url = fb.get(f"support/{cid}/meta/photo_url") or get_user_photo_url(msg.from_user.id)
    fb.patch(f"support/{cid}/meta", {
        "user_name": uname, "username": un,
        "chat_id": cid, "photo_url": photo_url,
    })

    store_message(cid, mid, {
        "msg_id":    mid, "chat_id": cid,
        "user_name": uname, "username": un,
        "text":      caption, "caption": caption,
        "file_id":   file_id, "file_url": file_url,
        "type":      "video", "from": "user",
        "time":      now_str(), "ts": now_ts(),
        "read":      False, "delivered": True, "edited": False,
    })

    mark_admin_msgs_read(cid)
    send_support_notify(cid, uname, caption or "[video]", "video")

# ── User sends document ───────────────────────────────────────────────────────
@support_bot.message_handler(content_types=["document"])
def handle_document(msg):
    cid   = str(msg.chat.id)
    mid   = str(msg.message_id)
    fn    = msg.from_user.first_name or ""
    un    = msg.from_user.username or ""
    uname = f"{fn} {msg.from_user.last_name or ''}".strip()

    if _is_blocked(cid):
        send_msg(cid, BLOCKED_MSG); return

    file_id   = msg.document.file_id
    file_name = msg.document.file_name or "file"
    caption   = msg.caption or ""
    file_url  = _get_file_url(file_id)

    photo_url = fb.get(f"support/{cid}/meta/photo_url") or get_user_photo_url(msg.from_user.id)
    fb.patch(f"support/{cid}/meta", {
        "user_name": uname, "username": un,
        "chat_id": cid, "photo_url": photo_url,
    })

    store_message(cid, mid, {
        "msg_id":    mid, "chat_id": cid,
        "user_name": uname, "username": un,
        "text":      caption, "caption": caption,
        "file_id":   file_id, "file_url": file_url,
        "file_name": file_name,
        "type":      "document", "from": "user",
        "time":      now_str(), "ts": now_ts(),
        "read":      False, "delivered": True, "edited": False,
    })

    mark_admin_msgs_read(cid)
    send_support_notify(cid, uname, caption or f"[{file_name}]", "document")

# ── Admin: send text / image_url ──────────────────────────────────────────────
def admin_reply(chat_id: str, text: str, image_url: str = "") -> dict:
    """Called by Flask /support/<cid>/send route."""
    if not support_bot:
        return {"error": "Support bot not running"}
    try:
        if image_url and text:
            m = support_bot.send_photo(chat_id, image_url, caption=text, parse_mode="Markdown")
        elif image_url:
            m = support_bot.send_photo(chat_id, image_url)
        else:
            m = support_bot.send_message(chat_id, text, parse_mode="Markdown")
        mid = str(m.message_id)

        fb.put(f"support/{chat_id}/messages/admin_{mid}", {
            "msg_id":    f"admin_{mid}",
            "chat_id":   chat_id,
            "text":      text,
            "image_url": image_url,
            "type":      "photo" if image_url else "text",
            "from":      "admin",
            "time":      now_str(),
            "ts":        now_ts(),
            "read":      False,
            "delivered": True,
            "edited":    False,
        })
        fb.patch(f"support/{chat_id}/meta", {
            "last_message": text or "[image]",
            "last_time":    now_str(),
            "last_ts":      now_ts(),
            "unread":       0,
        })
        return {"ok": True, "mid": mid}
    except Exception as e:
        return {"error": str(e)}

# ── Admin: send uploaded file ─────────────────────────────────────────────────
def admin_send_file(chat_id: str, file_bytes: bytes, filename: str,
                    mime_type: str, caption: str = "") -> dict:
    """Upload a file from the admin panel → Telegram → store Telegram CDN URL."""
    if not support_bot:
        return {"error": "Support bot not running"}
    try:
        f = io.BytesIO(file_bytes)
        f.name = filename
        cap = caption or None

        if mime_type.startswith("image/"):
            m      = support_bot.send_photo(chat_id, f, caption=cap, parse_mode="Markdown")
            fid    = m.photo[-1].file_id
            ftype  = "photo"
        elif mime_type.startswith("video/"):
            m      = support_bot.send_video(chat_id, f, caption=cap, parse_mode="Markdown")
            fid    = m.video.file_id
            ftype  = "video"
        else:
            m      = support_bot.send_document(chat_id, f, caption=cap, parse_mode="Markdown")
            fid    = m.document.file_id
            ftype  = "document"

        file_url = _get_file_url(fid)
        mid      = str(m.message_id)

        fb.put(f"support/{chat_id}/messages/admin_{mid}", {
            "msg_id":    f"admin_{mid}",
            "chat_id":   chat_id,
            "text":      caption,
            "file_url":  file_url,
            "file_name": filename,
            "type":      ftype,
            "from":      "admin",
            "time":      now_str(),
            "ts":        now_ts(),
            "read":      False,
            "delivered": True,
            "edited":    False,
        })
        fb.patch(f"support/{chat_id}/meta", {
            "last_message": caption or f"[{ftype}]",
            "last_time":    now_str(),
            "last_ts":      now_ts(),
            "unread":       0,
        })
        return {"ok": True, "mid": mid, "file_url": file_url, "type": ftype}
    except Exception as e:
        return {"error": str(e)}

# ── Admin: block / unblock ────────────────────────────────────────────────────
def block_user(chat_id: str) -> dict:
    fb.patch(f"support/{chat_id}/meta", {"blocked": True})
    try:
        support_bot.send_message(chat_id, BLOCKED_MSG)
    except: pass
    return {"ok": True}

def unblock_user(chat_id: str) -> dict:
    fb.patch(f"support/{chat_id}/meta", {"blocked": False})
    return {"ok": True}

# ── Admin: edit / delete ──────────────────────────────────────────────────────
def admin_edit_message(chat_id: str, admin_mid: str, new_text: str) -> dict:
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
