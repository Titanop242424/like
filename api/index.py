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
import random
import time
import os
import hashlib
from datetime import datetime, timedelta

app = Flask(__name__)

API_KEY = "TITAN"
TOKEN_CACHE_FILE = "tokens_cache.json"
TOKEN_EXPIRY_HOURS = 5

# ================= JWT API CONFIGURATION =================
# Replace this with the API address you can access normally
JWT_API_URL = "https://ff-jwt-gen-api.lovable.app/api/public/token"
# ==========================================

# ================= TOKEN CACHE MANAGEMENT (Simplified) =================
def load_token_cache():
    try:
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
                print(f"✅ Loaded token cache")
                return cache_data
        return {"tokens": [], "generated_at": None}
    except Exception as e:
        print(f"⚠️ Error loading token cache: {e}")
        return {"tokens": [], "generated_at": None}

def save_token_cache(cache_data):
    try:
        with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
        print(f"✅ Token cache saved")
        return True
    except Exception as e:
        print(f"❌ Error saving token cache: {e}")
        return False

def is_token_cache_valid(cache_data):
    if not cache_data or not cache_data.get("tokens"):
        return False
    generated_at = cache_data.get("generated_at")
    if not generated_at:
        return False
    try:
        gen_time = datetime.fromisoformat(generated_at)
        time_diff = datetime.now() - gen_time
        if time_diff.total_seconds() < (TOKEN_EXPIRY_HOURS * 3600):
            return True
        return False
    except:
        return False

def get_cached_tokens(limit=None):
    cache_data = load_token_cache()
    if not is_token_cache_valid(cache_data):
        return None
    tokens = cache_data.get("tokens", [])
    if limit and limit > 0:
        return tokens[:limit]
    return tokens

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

# ================= JWT ACQUISITION (EXTERNAL API VERSION) =================
def get_jwt_from_external_api(uid, password):
    """
    Obtain JWT through external API
    Returns JWT string on success, None on failure
    """
    try:
        # Construct request URL
        params = {
            "uid": uid,
            "password": password
        }
        
        print(f"   🌐 Requesting JWT for {uid} from external API...")
        
        # Send GET request
        response = requests.get(
            JWT_API_URL, 
            params=params, 
            timeout=20,
            verify=False
        )
        
        print(f"   📡 API Response Status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"   ⚠️ API error {response.status_code}: {response.text[:200]}")
            return None
        
        # Parse response
        try:
            data = response.json()
        except:
            # If response is not JSON, try to extract JWT from text
            text = response.text.strip()
            if text.startswith("eyJ"):
                print(f"   ✅ JWT extracted from text response")
                return text
            print(f"   ⚠️ Cannot parse response: {text[:200]}")
            return None
        
        # Try different possible response fields
        token = data.get("token") or data.get("jwt") or data.get("access_token")
        
        if not token:
            # Sometimes the response might directly be a string
            if isinstance(data, str) and data.startswith("eyJ"):
                token = data
            else:
                print(f"   ⚠️ No token field in response: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                return None
        
        # Validate JWT format
        if token and token.startswith("eyJ") and len(token) > 100:
            print(f"   ✅ JWT obtained (length: {len(token)})")
            return token
        else:
            print(f"   ⚠️ Invalid JWT format (length: {len(token) if token else 0})")
            return None
            
    except requests.exceptions.Timeout:
        print(f"   ❌ Timeout requesting JWT for {uid}")
        return None
    except requests.exceptions.ConnectionError:
        print(f"   ❌ Connection error requesting JWT for {uid}")
        return None
    except Exception as e:
        print(f"   ❌ Error requesting JWT for {uid}: {e}")
        return None

def get_token_for_account(acc):
    """
    Obtain JWT for a single account
    """
    uid = acc.get("uid")
    password = acc.get("password")
    
    if not uid or not password:
        print(f"   ⚠️ Missing uid or password for account")
        return None
    
    token = get_jwt_from_external_api(uid, password)
    
    if token:
        return {"token": token, "uid": uid}
    return None

def generate_all_tokens():
    """Obtain JWT for all accounts"""
    if not ACCOUNTS:
        print("❌ No accounts available")
        return []
    
    print(f"🔄 Generating tokens for {len(ACCOUNTS)} accounts via external API...")
    tokens = []
    
    for acc in ACCOUNTS:
        token_data = get_token_for_account(acc)
        if token_data:
            tokens.append(token_data)
        # Small delay to avoid rate limiting
        time.sleep(0.5)
    
    print(f"✅ Generated {len(tokens)} valid tokens out of {len(ACCOUNTS)} accounts")
    
    if tokens:
        update_token_cache(tokens)
    
    return tokens

def get_tokens(limit=None):
    """Get tokens from cache if valid, otherwise generate new ones"""
    cached_tokens = get_cached_tokens(limit)
    
    if cached_tokens is not None:
        if limit and limit > 0:
            if len(cached_tokens) >= limit:
                print(f"✅ Using {limit} cached tokens")
                return cached_tokens[:limit]
            else:
                print(f"⚠️ Cache has {len(cached_tokens)} tokens, need {limit}. Regenerating...")
        else:
            print(f"✅ Using all {len(cached_tokens)} cached tokens")
            return cached_tokens
    
    print("🔄 Generating fresh tokens...")
    return generate_all_tokens()

# ================= LIKE SENDING FUNCTIONS =================
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
            async with session.post(url, data=edata, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                print(f"   Like response for {token_dict.get('uid', 'unknown')}: {response.status}")
                return response.status
    except Exception as e:
        print(f"   Like error: {e}")
        return 997

async def send_likes_with_token_batch(uid, server_region, like_api_url, token_batch):
    like_protobuf_payload = create_protobuf_message(uid, server_region)
    encrypted_like_payload = encrypt_message(like_protobuf_payload)
    tasks = [send_single_like_request(encrypted_like_payload, t, like_api_url) for t in token_batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    successful = 0
    failed = 0
    for r in results:
        if isinstance(r, int) and r == 200:
            successful += 1
        elif isinstance(r, int) and r == 403:
            print(f"⚠️ 403 Forbidden - Token may be invalid")
            failed += 1
        elif isinstance(r, int) and r == 429:
            print(f"⚠️ 429 Rate limited")
            failed += 1
        else:
            failed += 1
    
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
    except:
        pass
    return 0

def get_name_from_info(info):
    try:
        if info and hasattr(info, 'AccountInfo'):
            if hasattr(info.AccountInfo, 'PlayerNickname'):
                return str(info.AccountInfo.PlayerNickname)
    except:
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

    print(f"📊 Processing like request for UID: {uid_param}, Region: {server_name_param}")

    start_time = time.time()
    token_limit = likes_limit if likes_limit > 0 else None
    fresh_tokens = get_tokens(token_limit)
    
    if not fresh_tokens:
        return jsonify({"error": "Failed to get any valid tokens. Check accounts.json and JWT API."}), 500

    token_time = time.time() - start_time
    print(f"⏱️ Token retrieval took {token_time:.2f} seconds")

    if likes_limit > 0 and len(fresh_tokens) > likes_limit:
        fresh_tokens = fresh_tokens[:likes_limit]
    
    print(f"✅ Using {len(fresh_tokens)} tokens")

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
        likes_sent, failed_count = loop.run_until_complete(send_likes_with_token_batch(uid_param, server_name_param, like_api_url, fresh_tokens))
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
    
    cache_data = load_token_cache()
    is_cached = is_token_cache_valid(cache_data)
    cache_expiry = cache_data.get("expires_at", "N/A") if cache_data else "N/A"
    
    requested_display = likes_limit if likes_limit > 0 else "ALL"
    response_data = {
        "LikesGivenByAPI": likes_given,
        "LikesafterCommand": after_likes,
        "LikesbeforeCommand": before_likes,
        "PlayerNickname": player_name,
        "UID": uid_param,
        "status": 1 if likes_given > 0 else 2,
        "RequestedLikes": requested_display,
        "LikesSent": likes_sent,
        "FailedLikes": failed_count,
        "TotalAccountsUsed": len(fresh_tokens),
        "TotalAccountsAvailable": len(ACCOUNTS),
        "TokenSource": "Cached" if is_cached else "Freshly Generated",
        "TokenExpiry": cache_expiry,
        "TimeStats": {
            "TotalTime": f"{total_time:.2f}s",
            "TokenRetrievalTime": f"{token_time:.2f}s",
            "LikeSendingTime": f"{send_time:.2f}s"
        },
        "Note": f"Used {len(fresh_tokens)} accounts. Tokens from external API.",
        "Owner": "@OPTITAN"
    }
    
    return jsonify(response_data)

@app.route('/refresh_tokens', methods=['GET'])
def refresh_tokens():
    api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")
    if api_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid API key."}), 401
    
    print("🔄 Manual token refresh requested")
    tokens = generate_all_tokens()
    
    return jsonify({
        "status": "success",
        "message": f"Generated {len(tokens)} fresh tokens via external API",
        "total_tokens": len(tokens),
        "total_accounts": len(ACCOUNTS)
    })

@app.route('/cache_status', methods=['GET'])
def cache_status():
    api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")
    if api_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid API key."}), 401
    
    cache_data = load_token_cache()
    is_valid = is_token_cache_valid(cache_data)
    
    return jsonify({
        "cache_exists": bool(cache_data and cache_data.get("tokens")),
        "is_valid": is_valid,
        "total_tokens": len(cache_data.get("tokens", [])) if cache_data else 0,
        "generated_at": cache_data.get("generated_at", "N/A") if cache_data else "N/A",
        "expires_at": cache_data.get("expires_at", "N/A") if cache_data else "N/A",
        "expiry_hours": TOKEN_EXPIRY_HOURS,
        "total_accounts": len(ACCOUNTS)
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
    
    return jsonify({
        "status": "online",
        "message": "Free Fire Like Bot API - External JWT API",
        "jwt_api": JWT_API_URL,
        "endpoints": {
            "/like": "Send likes with limit parameter",
            "/refresh_tokens": "Manually refresh all tokens",
            "/cache_status": "Check token cache status",
            "/accounts": "View all accounts"
        },
        "limit_usage": {
            "limit=10": "Try to send exactly 10 likes using 10 accounts",
            "limit=0": "Use all available accounts",
            "example": "/like?uid=123&server_name=IND&api_key=TITAN&limit=5"
        },
        "token_cache": {
            "status": "Valid" if is_valid else "Invalid/Expired",
            "cached_tokens": len(cache_data.get("tokens", [])) if cache_data else 0,
            "expiry_hours": TOKEN_EXPIRY_HOURS,
            "expires_at": cache_data.get("expires_at", "N/A") if cache_data else "N/A"
        },
        "total_accounts": len(ACCOUNTS)
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 1000))
    print(f"🚀 Like API Running on port {port}")
    print(f"📁 Loaded {len(ACCOUNTS)} accounts")
    print(f"⏰ Tokens valid for {TOKEN_EXPIRY_HOURS} hours")
    print(f"🔗 JWT API: {JWT_API_URL}")
    
    print("🔄 Pre-generating tokens...")
    generate_all_tokens()
    
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
