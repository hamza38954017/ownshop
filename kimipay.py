"""
kimipay.py — KimiPay payment gateway client
API: https://kimipay.in/api
Signature: MD5(sorted_params + &key=API_KEY).upper()
"""
import hashlib, requests
import firebase_helper as fb

TIMEOUT = 15

def _base():
    """Read API base URL from Firebase config, fallback to default."""
    url = (fb.get("config/kimipay_base_url") or "").strip().rstrip("/")
    return url if url else "https://kimipay.in/api"

def _creds():
    cfg = fb.get("config") or {}
    return cfg.get("kimipay_app_id",""), cfg.get("kimipay_api_key","")

def _sign(params: dict, api_key: str) -> str:
    s = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
    s += f"&key={api_key}"
    return hashlib.md5(s.encode()).hexdigest().upper()

def create_order(amount:int, order_sn:str, description:str="",
                 callback_url:str="", customer_email:str="") -> dict:
    app_id, api_key = _creds()
    if not app_id or not api_key:
        return {"error":"KimiPay not configured. Set kimipay_app_id and kimipay_api_key in Settings."}
    params = {"app_id":app_id,"merchant_api_key":api_key,
              "amount":amount,"order_sn":order_sn}
    if description:    params["description"]    = description
    if customer_email: params["customer_email"] = customer_email
    if callback_url:   params["callback_url"]   = callback_url
    sig_p = {k:v for k,v in params.items() if k not in ("callback_url","description","customer_email")}
    params["signature"] = _sign(sig_p, api_key)
    try:
        r = requests.post(f"{_base()}/order/create", json=params, timeout=TIMEOUT)
        d = r.json()
        if d.get("status") == 1:
            return {"success":True,"kimipay_order_id":d["data"]["order_id"],
                    "payment_url":d["data"]["payment_url"],
                    "expires_at":d["data"].get("expires_at"),
                    "amount":d["data"]["amount"]}
        return {"error":d.get("msg","Order creation failed")}
    except Exception as e:
        return {"error":str(e)}

def query_order(kimipay_order_id:str) -> dict:
    app_id, api_key = _creds()
    if not app_id or not api_key:
        return {"error":"KimiPay not configured","status":"unknown"}
    params = {"app_id":app_id,"merchant_api_key":api_key,"order_id":kimipay_order_id}
    params["signature"] = _sign(params, api_key)
    try:
        r = requests.post(f"{_base()}/order/query", json=params, timeout=TIMEOUT)
        d = r.json()
        if d.get("status") == 1:
            return {"success":True,"status":d["data"]["status"],
                    "amount":d["data"]["amount"],"paid_at":d["data"].get("paid_at")}
        return {"error":d.get("msg","Query failed"),"status":"pending"}
    except Exception as e:
        return {"error":str(e),"status":"unknown"}
