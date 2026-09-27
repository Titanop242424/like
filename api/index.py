from flask import Flask, request, jsonify
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import binascii
import aiohttp
import requests
import json
import like_pb2
import like_count_pb2
import uid_generator_pb2
import urllib3
import time
import os
import threading
from datetime import datetime, timedelta

# ✅ Upstash Redis REST client (works on Vercel / serverless)
from upstash_redis import Redis

app = Flask(__name__)

API_KEY = "TITAN"

# ================= CONFIGURATION =================
JWT_API_URL = "https://ff-jwt-gen-api.lovable.app/api/public/token"

TOKEN_EXPIRY_HOURS = 8
TOKEN_SAFETY_BUFFER_SEC = 60
CACHE_KEY = "ff_like:tokens_cache"

# ✅ NEW: Cooldown & rotation config
COOLDOWN_HOURS = 24
COOLDOWN_KEY_PREFIX = "ff_like:cooldown:"
ROTATION_KEY = "ff_like:account_rotation_index"
MAX_LIKES_PER_REQUEST = 100  # hard cap per request

# ✅ Upstash credentials (env vars preferred, fallbacks included)
UPSTASH_URL = os.environ.get(
    "UPSTASH_REDIS_REST_URL",
    "https://enhanced-caribou-284306.upstash.io"
)
UPSTASH_TOKEN = os.environ.get(
    "UPSTASH_REDIS_REST_TOKEN",
    "gQAAAAAABFaSAAIgcDE1YmFlZTYxZTM0OTQ0NmIwOTNjMjZhNjJkOWM5MWRmMw"
)

# ---- Redis client ----
redis_client = None
try:
    redis_client = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
    redis_client.set("__healthcheck__", "ok", ex=10)
    assert redis_client.get("__healthcheck__") == "ok"
    print("✅ Connected to Upstash Redis")
except Exception as e:
    print(f"❌ Upstash Redis connection failed: {e}")
    redis_client = None

_token_lock = threading.Lock()


# ================= NEW: COOLDOWN HELPERS =================
def get_cooldown_key(uid: str) -> str:
    return f"{COOLDOWN_KEY_PREFIX}{uid}"


def check_cooldown(uid: str):
    """
    Returns (is_on_cooldown, seconds_remaining, last_like_time_iso).
    If no cooldown active, returns (False, 0, None).
    """
    if not redis_client:
        return False, 0, None
    try:
        key = get_cooldown_key(uid)
        raw = redis_client.get(key)
        if not raw:
            return False, 0, None

        data = json.loads(raw)
        last_time = datetime.fromisoformat(data["last_like_time"])
        elapsed = (datetime.now() - last_time).total_seconds()
        cooldown_sec = COOLDOWN_HOURS * 3600

        if elapsed < cooldown_sec:
            remain = int(cooldown_sec - elapsed)
            return True, remain, data["last_like_time"]
        return False, 0, None
    except Exception as e:
        print(f"⚠️ Cooldown check error: {e}")
        return False, 0, None


def set_cooldown(uid: str):
    """Mark UID as 'liked now' with 24h TTL."""
    if not redis_client:
        return
    try:
        key = get_cooldown_key(uid)
        payload = {
            "last_like_time": datetime.now().isoformat(),
            "uid": uid
        }
        redis_client.set(key, json.dumps(payload), ex=COOLDOWN_HOURS * 3600)
        print(f"⏱️ Cooldown set for UID {uid} (TTL={COOLDOWN_HOURS}h)")
    except Exception as e:
        print(f"⚠️ Cooldown set error: {e}")


def format_remaining(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m}m {s}s"


# ================= NEW: ACCOUNT ROTATION =================
def get_rotated_tokens(all_tokens, count):
    """
    Return `count` tokens starting from a rotating offset, so we don't
    always pick the first N accounts. Wraps around the list.
    """
    if not all_tokens:
        return []
    if count >= len(all_tokens):
        return list(all_tokens)

    # Get current offset from Redis (or 0)
    offset = 0
    if redis_client:
        try:
            raw = redis_client.get(ROTATION_KEY)
            if raw:
                offset = int(raw)
        except Exception:
            offset = 0

    offset = offset % len(all_tokens)
    rotated = all_tokens[offset:] + all_tokens[:offset]

    # Advance offset for next call
    new_offset = (offset + count) % len(all_tokens)
    if redis_client:
        try:
            redis_client.set(ROTATION_KEY, str(new_offset))
        except Exception:
            pass

    return rotated[:count]


# ================= TOKEN CACHE =================
def load_token_cache():
    if not redis_client:
        return {"tokens": [], "generated_at": None, "expires_at": None}
    try:
        raw = redis_client.get(CACHE_KEY)
        if not raw:
            return {"tokens": [], "generated_at": None, "expires_at": None}
        return json.loads(raw)
    except Exception as e:
        print(f"❌ [cache] Load error: {e}")
        return {"tokens": [], "generated_at": None, "expires_at": None}


def save_token_cache(cache_data):
    if not redis_client:
        return False
    try:
        ttl_seconds = int(TOKEN_EXPIRY_HOURS * 3600)
        redis_client.set(CACHE_KEY, json.dumps(cache_data), ex=ttl_seconds)
        return True
    except Exception as e:
        print(f"❌ [cache] Save error: {e}")
        return False


def is_token_cache_valid(cache_data):
    if not cache_data or not cache_data.get("tokens"):
        return False
    generated_at = cache_data.get("generated_at")
    if not generated_at:
        return False
    try:
        gen_time = datetime.fromisoformat(generated_at)
        age_sec = (datetime.now() - gen_time).total_seconds()
        max_age_sec = (TOKEN_EXPIRY_HOURS * 3600) - TOKEN_SAFETY_BUFFER_SEC
        return age_sec < max_age_sec
    except Exception:
        return False


def get_cached_tokens():
    cache_data = load_token_cache()
    if not is_token_cache_valid(cache_data):
        return None
    return cache_data.get("tokens", [])


def seconds_until_expiry(cache_data):
    try:
        expires_at = cache_data.get("expires_at")
        if not expires_at:
            return 0
        delta = datetime.fromisoformat(expires_at) - datetime.now()
        return max(0, int(delta.total_seconds()))
    except Exception:
        return 0


def update_token_cache(new_tokens):
    cache_data = {
        "tokens": new_tokens,
        "generated_at": datetime.now().isoformat(),
        "total_tokens": len(new_tokens),
        "expires_at": (datetime.now() + timedelta(hours=TOKEN_EXPIRY_HOURS)).isoformat()
    }
    save_token_cache(cache_data)
    return cache_data


# ================= LOAD ACCOUNTS =================
def load_accounts():
    try:
        if os.path.exists("accounts.json"):
            with open("accounts.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    accounts = [acc for acc in data if acc.get("enabled", True)]
                    print(f"✅ Loaded {len(accounts)} accounts")
                    return accounts
        print("⚠️ accounts.json not found!")
        return []
    except Exception as e:
        print(f"❌ Error loading accounts.json: {e}")
        return []


ACCOUNTS = load_accounts()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ================= ENCRYPTION =================
def encrypt_message(plaintext: bytes) -> str:
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded_message = pad(plaintext, AES.block_size)
    encrypted_message = cipher.encrypt(padded_message)
    return binascii.hexlify(encrypted_message).decode('utf-8')


# ================= PROTOBUF =================
def create_protobuf_message(user_id: int, region: str) -> bytes:
    message = like_pb2.like()
    message.uid = int(user_id)
    message.region = region
    return message.SerializeToString()


def create_protobuf_for_profile_check(uid: int) -> bytes:
    message = uid_generator_pb2.uid_generator()
    message.krishna_ = int(uid)
    message.teamXdarks = 1
    return message.SerializeToString()


def enc_profile_check_payload(uid: int) -> str:
    protobuf_data = create_protobuf_for_profile_check(uid)
    return encrypt_message(protobuf_data)


# ================= JWT ACQUISITION =================
def get_jwt_from_external_api(uid, password):
    try:
        params = {"uid": uid, "password": password}
        response = requests.get(JWT_API_URL, params=params, timeout=20, verify=False)
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except Exception:
            text = response.text.strip()
            if text.startswith("eyJ"):
                return text
            return None
        token = data.get("token") or data.get("jwt") or data.get("access_token")
        if not token:
            if isinstance(data, str) and data.startswith("eyJ"):
                token = data
            else:
                return None
        if token and token.startswith("eyJ") and len(token) > 100:
            return token
        return None
    except Exception as e:
        print(f"   ❌ Error requesting JWT for {uid}: {e}")
        return None


def get_token_for_account(acc):
    uid = acc.get("uid")
    password = acc.get("password")
    if not uid or not password:
        return None
    token = get_jwt_from_external_api(uid, password)
    if token:
        return {"token": token, "uid": uid}
    return None


def generate_all_tokens():
    with _token_lock:
        cached = get_cached_tokens()
        if cached:
            return cached
        if not ACCOUNTS:
            return []
        tokens = []
        for acc in ACCOUNTS:
            token_data = get_token_for_account(acc)
            if token_data:
                tokens.append(token_data)
            time.sleep(0.5)
        if tokens:
            update_token_cache(tokens)
        return tokens


def get_tokens():
    """Return full token list (no slicing here — rotation handles selection)."""
    cached = get_cached_tokens()
    if cached:
        return cached
    return generate_all_tokens()


# ================= LIKE SENDING =================
async def send_single_like_request(encrypted_like_payload, token_dict, url):
    edata = bytes.fromhex(encrypted_like_payload)
    token_value = token_dict.get("token", "")
    if not token_value:
        return 999
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token_value}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB55"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data=edata, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                return response.status
    except Exception:
        return 997


async def send_likes_with_token_batch(uid, server_region, like_api_url, token_batch):
    like_protobuf_payload = create_protobuf_message(uid, server_region)
    encrypted_like_payload = encrypt_message(like_protobuf_payload)
    tasks = [
        send_single_like_request(encrypted_like_payload, t, like_api_url)
        for t in token_batch
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successful = sum(1 for r in results if isinstance(r, int) and r == 200)
    failed = len(results) - successful
    return successful, failed


# ================= PROFILE CHECK =================
def make_profile_check_request(encrypted_profile_payload, server_name, token_dict):
    token_value = token_dict.get("token", "")
    if not token_value:
        return None
    if server_name == "IND":
        url = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        url = "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    else:
        url = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"
    edata = bytes.fromhex(encrypted_profile_payload)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token_value}",
        'Content-Type': "application/x-www-form-urlencoded",
        'ReleaseVersion': "OB55",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'Accept-Encoding': "gzip"
    }
    try:
        resp = requests.post(url, data=edata, headers=headers, verify=False, timeout=15)
        if resp.status_code == 200:
            info = like_count_pb2.Info()
            info.ParseFromString(resp.content)
            return info
    except Exception as e:
        print(f"Profile check error: {e}")
    return None


def get_likes_from_info(info):
    try:
        if info and hasattr(info, 'AccountInfo'):
            if hasattr(info.AccountInfo, 'Likes'):
                return int(info.AccountInfo.Likes)
    except Exception:
        pass
    return 0


def get_name_from_info(info):
    try:
        if info and hasattr(info, 'AccountInfo'):
            if hasattr(info.AccountInfo, 'PlayerNickname'):
                return str(info.AccountInfo.PlayerNickname)
    except Exception:
        pass
    return "N/A"


# ================= FLASK ROUTES =================
@app.route('/like', methods=['GET'])
def handle_requests():
    api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")
    if api_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid API key."}), 401

    uid_param = request.args.get("uid")
    server_name_param = request.args.get("server_name", "").upper()

    likes_limit_param = request.args.get("limit")
    if likes_limit_param is not None:
        try:
            likes_limit = int(likes_limit_param)
            if likes_limit < 0:
                likes_limit = 0
        except ValueError:
            return jsonify({"error": "Invalid limit value. Must be a number."}), 400
    else:
        likes_limit = 0

    if not uid_param or not server_name_param:
        return jsonify({"error": "UID and server_name are required"}), 400

    # ========== NEW: 24h COOLDOWN CHECK ==========
    on_cd, remain_sec, last_time = check_cooldown(uid_param)
    if on_cd:
        return jsonify({
            "status": 429,
            "error": "Cooldown active",
            "message": f"UID {uid_param} already received likes recently. "
                       f"Please wait before requesting again.",
            "UID": uid_param,
            "LastLikedAt": last_time,
            "CooldownHours": COOLDOWN_HOURS,
            "SecondsRemaining": remain_sec,
            "RemainingTime": format_remaining(remain_sec),
            "Owner": "@OPTITAN"
        }), 429

    print(f"📊 Processing like request for UID: {uid_param}, Region: {server_name_param}")
    start_time = time.time()

    # ========== NEW: Fetch ALL tokens, then rotate-select ==========
    all_tokens = get_tokens()
    if not all_tokens:
        return jsonify({
            "error": "Failed to get any valid tokens. Check accounts.json and JWT API."
        }), 500

    total_available = len(all_tokens)

    # Determine how many to use
    if likes_limit > 0:
        requested = min(likes_limit, total_available, MAX_LIKES_PER_REQUEST)
    else:
        # Use all accounts but cap to MAX_LIKES_PER_REQUEST
        requested = min(total_available, MAX_LIKES_PER_REQUEST)

    # Rotate selection so we don't always hit first N
    fresh_tokens = get_rotated_tokens(all_tokens, requested)

    token_time = time.time() - start_time

    print(f"✅ Using {len(fresh_tokens)}/{total_available} tokens (rotated)")

    visit_token = fresh_tokens[0]
    encrypted_profile = enc_profile_check_payload(int(uid_param))

    before_info = make_profile_check_request(encrypted_profile, server_name_param, visit_token)
    before_likes = get_likes_from_info(before_info)
    player_name = get_name_from_info(before_info)
    print(f"📊 Before likes: {before_likes}")

    if server_name_param == "IND":
        like_api_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name_param in {"BR", "US", "SAC", "NA"}:
        like_api_url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        like_api_url = "https://clientbp.ggpolarbear.com/LikeProfile"

    send_start = time.time()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        likes_sent, failed_count = loop.run_until_complete(
            send_likes_with_token_batch(
                uid_param, server_name_param, like_api_url, fresh_tokens
            )
        )
    finally:
        loop.close()
    send_time = time.time() - send_start

    print(f"❤️ Likes sent successfully: {likes_sent}, Failed: {failed_count}")

    time.sleep(2)
    after_info = make_profile_check_request(encrypted_profile, server_name_param, visit_token)
    after_likes = get_likes_from_info(after_info)
    print(f"📊 After likes: {after_likes}")

    likes_given = after_likes - before_likes
    total_time = time.time() - start_time

    # ========== NEW: Set cooldown AFTER successful like ==========
    if likes_given > 0:
        set_cooldown(uid_param)

    cache_data = load_token_cache()
    is_cached_now = is_token_cache_valid(cache_data)
    cache_expiry = cache_data.get("expires_at", "N/A")
    remain_sec_cache = seconds_until_expiry(cache_data)

    response_data = {
        "LikesGivenByAPI": likes_given,
        "LikesafterCommand": after_likes,
        "LikesbeforeCommand": before_likes,
        "PlayerNickname": player_name,
        "UID": uid_param,
        "status": 1 if likes_given > 0 else 2,
        "RequestedLikes": likes_limit if likes_limit > 0 else "ALL",
        "LikesSent": likes_sent,
        "FailedLikes": failed_count,
        "AccountsUsedThisRequest": len(fresh_tokens),
        "TotalAccountsAvailable": total_available,
        "RotationEnabled": True,
        "CooldownSetForHours": COOLDOWN_HOURS if likes_given > 0 else 0,
        "TokenSource": "Cached" if is_cached_now else "Freshly Generated",
        "TokenCacheValidNow": is_cached_now,
        "TokenExpiry": cache_expiry,
        "TokenSecondsRemaining": remain_sec_cache,
        "TimeStats": {
            "TotalTime": f"{total_time:.2f}s",
            "TokenRetrievalTime": f"{token_time:.2f}s",
            "LikeSendingTime": f"{send_time:.2f}s"
        },
        "CacheBackend": "Upstash Redis",
        "Owner": "@OPTITAN"
    }
    return jsonify(response_data)


@app.route('/cooldown_status', methods=['GET'])
def cooldown_status():
    """NEW: Check cooldown status for a UID."""
    api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")
    if api_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid API key."}), 401

    uid_param = request.args.get("uid")
    if not uid_param:
        return jsonify({"error": "uid is required"}), 400

    on_cd, remain_sec, last_time = check_cooldown(uid_param)
    return jsonify({
        "UID": uid_param,
        "OnCooldown": on_cd,
        "LastLikedAt": last_time,
        "CooldownHours": COOLDOWN_HOURS,
        "SecondsRemaining": remain_sec,
        "RemainingTime": format_remaining(remain_sec) if on_cd else "0h 0m 0s",
        "Owner": "@OPTITAN"
    })


@app.route('/refresh_tokens', methods=['GET'])
def refresh_tokens():
    api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")
    if api_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid API key."}), 401

    try:
        if redis_client:
            redis_client.delete(CACHE_KEY)
    except Exception as e:
        print(f"⚠️ Could not clear Redis cache: {e}")

    tokens = generate_all_tokens()
    return jsonify({
        "status": "success",
        "message": f"Generated {len(tokens)} fresh tokens via external API",
        "total_tokens": len(tokens),
        "total_accounts": len(ACCOUNTS),
        "expires_in_hours": TOKEN_EXPIRY_HOURS,
        "cache_backend": "Upstash Redis"
    })


@app.route('/cache_status', methods=['GET'])
def cache_status():
    api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")
    if api_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid API key."}), 401

    cache_data = load_token_cache()
    is_valid = is_token_cache_valid(cache_data)

    rotation_index = 0
    if redis_client:
        try:
            raw = redis_client.get(ROTATION_KEY)
            if raw:
                rotation_index = int(raw)
        except Exception:
            pass

    return jsonify({
        "cache_backend": "Upstash Redis" if redis_client else "DISABLED (no Redis)",
        "cache_exists": bool(cache_data and cache_data.get("tokens")),
        "is_valid": is_valid,
        "total_tokens": len(cache_data.get("tokens", [])),
        "generated_at": cache_data.get("generated_at", "N/A"),
        "expires_at": cache_data.get("expires_at", "N/A"),
        "seconds_remaining": seconds_until_expiry(cache_data),
        "expiry_hours": TOKEN_EXPIRY_HOURS,
        "safety_buffer_seconds": TOKEN_SAFETY_BUFFER_SEC,
        "total_accounts": len(ACCOUNTS),
        "rotation_index": rotation_index,
        "cooldown_hours": COOLDOWN_HOURS,
        "max_likes_per_request": MAX_LIKES_PER_REQUEST
    })


@app.route('/accounts', methods=['GET'])
def view_accounts():
    return jsonify({
        "total": len(ACCOUNTS),
        "accounts": ACCOUNTS
    })


@app.route('/', methods=['GET'])
def home():
    cache_data = load_token_cache()
    is_valid = is_token_cache_valid(cache_data)
    remain = seconds_until_expiry(cache_data)

    return jsonify({
        "status": "online",
        "message": "Free Fire Like Bot API — Upstash Redis cache (8h) + 24h UID cooldown + account rotation",
        "jwt_api": JWT_API_URL,
        "cache_backend": "Upstash Redis" if redis_client else "DISABLED",
        "endpoints": {
            "/like": "Send likes (24h cooldown per UID, rotated accounts)",
            "/cooldown_status": "Check cooldown for a UID (?uid=xxx)",
            "/refresh_tokens": "Manually refresh all tokens",
            "/cache_status": "Check token cache status",
            "/accounts": "View all accounts"
        },
        "limit_usage": {
            "limit=10": "Send ~10 likes using 10 rotated accounts",
            "limit=0": "Use all available accounts (up to cap)",
            "example": "/like?uid=123&server_name=IND&api_key=TITAN&limit=5"
        },
        "cooldown": {
            "hours": COOLDOWN_HOURS,
            "description": "Each UID can only receive likes once per 24h"
        },
        "rotation": {
            "enabled": True,
            "description": "Accounts are rotated so all get used fairly"
        },
        "max_likes_per_request": MAX_LIKES_PER_REQUEST,
        "token_cache": {
            "status": "Valid" if is_valid else "Invalid/Expired",
            "cached_tokens": len(cache_data.get("tokens", [])),
            "expiry_hours": TOKEN_EXPIRY_HOURS,
            "expires_at": cache_data.get("expires_at", "N/A"),
            "seconds_remaining": remain
        },
        "total_accounts": len(ACCOUNTS)
    })


# ================= STARTUP =================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 1000))
    print(f"🚀 Like API Running on port {port}")
    print(f"📁 Loaded {len(ACCOUNTS)} accounts")
    print(f"⏰ Tokens valid for {TOKEN_EXPIRY_HOURS} hours")
    print(f"⏱️ Cooldown: {COOLDOWN_HOURS}h per UID")
    print(f"🔗 JWT API: {JWT_API_URL}")

    existing = get_cached_tokens()
    if existing:
        print(f"✅ Startup: reusing {len(existing)} cached tokens")
    else:
        print("🔄 Startup: generating tokens...")
        generate_all_tokens()

    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
