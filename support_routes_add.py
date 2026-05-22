# ─────────────────────────────────────────────────────────────────────────────
# ADD THESE ROUTES to your main Flask app (app.py / routes.py).
# They wire the admin panel buttons to support_bot.py functions.
# ─────────────────────────────────────────────────────────────────────────────
from flask import Blueprint, request, jsonify, render_template, redirect, url_for
import support_bot as sb
import firebase_helper as fb

support_bp = Blueprint("support_bp", __name__, url_prefix="/support")

# ── helpers ──────────────────────────────────────────────────────────────────
def _get_messages(cid):
    raw = fb.get(f"support/{cid}/messages") or {}
    msgs = sorted(raw.values(), key=lambda m: m.get("ts", 0))
    return msgs

def _get_meta(cid):
    return fb.get(f"support/{cid}/meta") or {}

# ── list all chats ────────────────────────────────────────────────────────────
@support_bp.route("/", endpoint="support")
def support():
    raw = fb.get("support") or {}
    chats = []
    for cid, data in raw.items():
        meta = data.get("meta", {})
        if meta:
            chats.append(meta)
    chats.sort(key=lambda c: c.get("last_ts", 0), reverse=True)
    return render_template("support.html", chats=chats)

# ── open single chat ──────────────────────────────────────────────────────────
@support_bp.route("/<cid>", endpoint="support_chat")
def support_chat(cid):
    meta     = _get_meta(cid)
    messages = _get_messages(cid)
    return render_template("support_chat.html", cid=cid, meta=meta, messages=messages)

# ── send text / image_url (existing) ─────────────────────────────────────────
@support_bp.route("/<cid>/send", methods=["POST"])
def support_send(cid):
    text      = request.form.get("text", "").strip()
    image_url = request.form.get("image_url", "").strip()
    result    = sb.admin_reply(cid, text, image_url)
    return jsonify(result)

# ── send uploaded file → Telegram → store CDN URL ────────────────────────────
@support_bp.route("/<cid>/send_file", methods=["POST"])
def support_send_file(cid):
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided"}), 400

    caption   = request.form.get("text", "").strip()
    file_bytes = f.read()
    filename   = f.filename or "file"
    mime_type  = f.content_type or "application/octet-stream"

    result = sb.admin_send_file(cid, file_bytes, filename, mime_type, caption)
    return jsonify(result)

# ── block user ────────────────────────────────────────────────────────────────
@support_bp.route("/<cid>/block", methods=["POST"])
def support_block(cid):
    result = sb.block_user(cid)
    return jsonify(result)

# ── unblock user ──────────────────────────────────────────────────────────────
@support_bp.route("/<cid>/unblock", methods=["POST"])
def support_unblock(cid):
    result = sb.unblock_user(cid)
    return jsonify(result)

# ── get messages as JSON (for polling) ───────────────────────────────────────
@support_bp.route("/<cid>/messages")
def support_messages(cid):
    return jsonify(_get_messages(cid))

# ── edit admin message ────────────────────────────────────────────────────────
@support_bp.route("/<cid>/message/<mid>/edit", methods=["POST"])
def support_edit(cid, mid):
    text = request.form.get("text", "").strip()
    return jsonify(sb.admin_edit_message(cid, mid, text))

# ── delete admin message ──────────────────────────────────────────────────────
@support_bp.route("/<cid>/message/<mid>/delete", methods=["POST"])
def support_delete(cid, mid):
    return jsonify(sb.admin_delete_message(cid, mid))

# ── clear all messages ────────────────────────────────────────────────────────
@support_bp.route("/<cid>/clear", methods=["POST"], endpoint="support_clear")
def support_clear(cid):
    fb.delete(f"support/{cid}/messages")
    fb.patch(f"support/{cid}/meta", {"last_message": "", "unread": 0})
    return redirect(url_for("support_bp.support_chat", cid=cid))

# ── unread badge count ────────────────────────────────────────────────────────
@support_bp.route("/unread_count")
def support_unread_count():
    raw   = fb.get("support") or {}
    total = sum(
        (data.get("meta", {}).get("unread") or 0)
        for data in raw.values()
    )
    return jsonify({"count": total})


# ─────────────────────────────────────────────────────────────────────────────
# In your main app.py, register the blueprint:
#
#   from support_routes_add import support_bp
#   app.register_blueprint(support_bp)
#
# ─────────────────────────────────────────────────────────────────────────────
