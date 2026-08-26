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
from datetime import datetime

app = Flask(__name__)

API_KEY = "TITAN"

# ================= LOAD ACCOUNTS FROM accounts.json =================
def load_accounts():
    try:
        # For Vercel, check multiple possible paths
        possible_paths = [
            "accounts.json",
            "/tmp/accounts.json",
            os.path.join(os.path.dirname(__file__), "..", "accounts.json"),
            os.path.join(os.path.dirname(__file__), "accounts.json")
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        accounts = [acc for acc in data if acc.get("enabled", True)]
                        print(f"✅ Loaded {len(accounts)} accounts from {path}")
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

# ================= PROTOBUF HELPERS =================
def encode_varint(n):
    result = []
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            result.append(b | 0x80)
        else:
            result.append(b)
            break
    return bytes(result)

def build_proto(fields):
    payload = b''
    for k, v in fields.items():
        if isinstance(v, int):
            tag = (k << 3) | 0
            payload += encode_varint(tag) + encode_varint(v)
        elif isinstance(v, (str, bytes)):
            data = v.encode() if isinstance(v, str) else v
            tag = (k << 3) | 2
            payload += encode_varint(tag) + encode_varint(len(data)) + data
    return payload

def encrypt_api(data):
    cipher = AES.new(b'Yg&tc%DEuh6%Zc^8', AES.MODE_CBC, b'6oyZDr22E3ychjM%')
    return cipher.encrypt(pad(data, AES.block_size))

# ================= TOKEN GENERATOR =================
def get_fresh_tokens():
    """accounts.json se accounts lekar direct token generate karega"""
    if not ACCOUNTS:
        print("❌ No accounts available")
        return []
    
    tokens = []
    for acc in ACCOUNTS:
        try:
            uid = acc.get("uid")
            password = acc.get("password")
            if not uid or not password:
                continue
            
            # ===== GET ACCESS TOKEN =====
            url = "https://100067.connect.garena.com/oauth/guest/token/grant"
            
            data = {
                "uid": uid,
                "password": password,
                "response_type": "token",
                "client_type": "2",
                "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
                "client_id": "100067"
            }
            
            headers = {
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13)",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            resp = requests.post(url, data=data, headers=headers, verify=False, timeout=15)
            
            if resp.status_code == 200:
                resp_data = resp.json()
                access_token = resp_data.get("access_token")
                open_id = resp_data.get("open_id")
                
                if access_token and open_id:
                    # ===== CONVERT TO JWT VIA MAJORLOGIN =====
                    jwt_url = "https://loginbp.ggpolarbear.com/MajorLogin"
                    
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    payload_fields = {
                        3: now, 4: "free fire", 5: 1,
                        7: "1.126.5",
                        8: "Android OS 5.1.1 / API-22",
                        9: "Handheld", 10: "Reliance Jio",
                        11: "WIFI", 17: "Adreno (TM) 640",
                        18: "OpenGL ES 3.0",
                        19: f"Google|{random.randint(1000000000, 9999999999)}",
                        20: f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
                        21: "en", 22: open_id,
                        23: 4, 24: "Handheld",
                        25: "Samsung SM-G998B",
                        26: "IND",
                        29: access_token,
                        33: "Reliance Jio", 34: "WIFI",
                        37: "7428b253defc164018c604a1ebbfebdf",
                        73: "/data/app/com.dts.freefireth-1/lib/arm",
                        75: f"{random.randint(100000000,999999999)}|/data/app/com.dts.freefireth-1/base.apk",
                        76: 2, 78: 2, 79: 2,
                        83: "OpenGLES2", 85: "Mumbai",
                        87: "android",
                        88: "KqsHT8nWdkA7u/m7k8vg2H5FgrCGa4lfww3nHBGRHRPwDFV4LyCj8sT23O/P6K06qC3MOLZRThwWwul+g2goHwtQJy8=",
                        90: '{"cur_rate":null,"support_etc2":false}',
                        97: 1, 98: 1, 99: "4", 100: "4"
                    }
                    
                    proto_bytes = build_proto(payload_fields)
                    encrypted = encrypt_api(proto_bytes)
                    
                    jwt_headers = {
                        "Accept": "*/*",
                        "Accept-Encoding": "deflate, gzip",
                        "Authorization": "Bearer",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Host": "loginbp.ggpolarbear.com",
                        "ReleaseVersion": "OB54",
                        "User-Agent": "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
                        "X-GA": "v1 1",
                        "X-Unity-Version": "2022.3.47f1"
                    }
                    
                    jwt_resp = requests.post(jwt_url, headers=jwt_headers, data=encrypted, verify=False, timeout=15)
                    
                    if jwt_resp.status_code == 200:
                        text = jwt_resp.text
                        jwt_start = text.find("eyJ")
                        if jwt_start != -1:
                            jwt_token = text[jwt_start:]
                            second_dot = jwt_token.find(".", jwt_token.find(".") + 1)
                            if second_dot != -1:
                                jwt_token = jwt_token[:second_dot + 44]
                                if jwt_token.startswith("eyJ"):
                                    tokens.append({"token": jwt_token, "uid": uid})
                                    print(f"✅ Token generated for {uid}")
                                    continue
                    
                    print(f"⚠️ JWT generation failed for {uid}")
                else:
                    print(f"⚠️ Token generation failed for {uid}")
        except Exception as e:
            print(f"❌ Error for {uid}: {e}")
    
    return tokens

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
        'ReleaseVersion': "OB54"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                response_text = await response.text()
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
    for r in results:
        if isinstance(r, int) and r == 200:
            successful += 1
        elif isinstance(r, int) and r == 403:
            print(f"⚠️ 403 Forbidden - Token may be invalid")
        elif isinstance(r, int) and r == 429:
            print(f"⚠️ 429 Rate limited")
    
    return successful

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
        'ReleaseVersion': "OB54",
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
    
    if not uid_param or not server_name_param:
        return jsonify({"error": "UID and server_name are required"}), 400

    print(f"📊 Processing like request for UID: {uid_param}, Region: {server_name_param}")
    
    fresh_tokens = get_fresh_tokens()
    
    if not fresh_tokens:
        return jsonify({"error": "Failed to generate fresh tokens. Check accounts.json."}), 500

    print(f"✅ Generated {len(fresh_tokens)} tokens")

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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        likes_sent = loop.run_until_complete(send_likes_with_token_batch(uid_param, server_name_param, like_api_url, fresh_tokens))
    finally:
        loop.close()
        
    print(f"❤️ Likes sent: {likes_sent}")
    
    time.sleep(2)
    after_info = make_profile_check_request(encrypted_profile, server_name_param, visit_token)
    after_likes = get_likes_from_info(after_info)
    
    print(f"📊 After likes: {after_likes}")
    
    likes_given = after_likes - before_likes
    
    return jsonify({
        "LikesGivenByAPI": likes_given,
        "LikesafterCommand": after_likes,
        "LikesbeforeCommand": before_likes,
        "PlayerNickname": player_name,
        "UID": uid_param,
        "status": 1 if likes_given > 0 else 2,
        "Note": f"Generated {len(fresh_tokens)} fresh tokens.",
        "Owner": "@XEROX_MODS",
        "Tg": "SEXTYMODS"
    })

@app.route('/accounts', methods=['GET'])
def view_accounts():
    return jsonify({
        "total": len(ACCOUNTS),
        "accounts": ACCOUNTS
    })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "message": "Free Fire Like Bot API",
        "endpoint": "/like?uid=UID&server_name=REGION&api_key=TITAN",
        "accounts_file": "accounts.json",
        "total_accounts": len(ACCOUNTS)
    })

# Vercel handler
def handler(request, context):
    return app(request, context)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 1000))
    print(f"🚀 Like API Running on port {port}")
    print(f"📁 Loaded {len(ACCOUNTS)} accounts from accounts.json")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
