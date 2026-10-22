import json
import logging
import asyncio
import re
import os
import time
import requests
import asyncpg
import random
import string
import uuid as _uuid
import threading
import aiohttp
from concurrent.futures import ThreadPoolExecutor, as_completed
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote as urllib_quote

# ==================== CẤU HÌNH NGÂN HÀNG & BOT ====================
API_TOKEN = '8817044998:AAEe0u1KgQF2-xXwCJ5aABdDu5hFx8w3LsQ'
ADMIN_ID = 7718090377
BANK_ID = "MB"
BANK_ACCOUNT = "0356442864"
ACCOUNT_NAME = "NGUYEN DIEN TUAN KIET"
SUPPORT_TELEGRAM = "@kietnguyen0999"
SUPPORT_ZALO = "0356442864"
WEBHOOK_HOST = '0.0.0.0'
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "spsk_test_zFCU1AguPj8T7RqzMAMRxSbgaspYi99y")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:fVXjKs8XvC9lljvT@db.xfyfbpqyelrzfsgwhgbc.supabase.co:5432/postgres")
SELF_URL = "https://botbanhang-s6iq.onrender.com/"
BOT_TELE = "@ToolTtc_bot"
LICENSE_APP_CODE = os.getenv("LICENSE_APP_CODE", "NVC_TTC_FACEBOOK_MANAGER")

# ==================== CẤU HÌNH PAGE CREATOR ====================
PAGE_CREATE_UNIT_PRICE     = 300
PAGE_CREATE_MAX_TOKENS     = 200
PAGE_CREATE_THREADS        = 20

# ─── Cấu hình nghỉ mặc định ───
PAGE_CREATE_DELAY_SUCCESS  = 300
PAGE_CREATE_DELAY_FAIL     = 60
PAGE_CREATE_MAX_PER_TOKEN  = 15

# ─── Giới hạn custom ───
DELAY_OK_MIN   = 30
DELAY_OK_MAX   = 1800
DELAY_FAIL_MIN = 10
DELAY_FAIL_MAX = 600

# ─── Ưu đãi user mới ───
FREE_PAGES_FOR_NEW_USER = 10   # 10 page OK đầu tiên miễn phí

# Headers check live
CHECK_HEADERS = {
    'accept': '*/*',
    'accept-language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
    'origin': 'https://timuid.com',
    'referer': 'https://timuid.com/',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
}

# 🟢 KHỞI TẠO
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
db_pool = None

# ==================== FSM STATES ====================
class BuyState(StatesGroup):
    waiting_for_quantity = State()

class PageCreateState(StatesGroup):
    waiting_for_tokens       = State()
    waiting_for_max_pages    = State()
    waiting_for_name_style   = State()
    waiting_for_delay_choice = State()
    waiting_for_delay_ok     = State()
    waiting_for_delay_fail   = State()

class AdminFreeState(StatesGroup):
    waiting_for_reset_user = State()

# ==================== HÀM CHECK LIVE & LỌC TRÙNG UID FB ====================
def check_single_uid(account_info):
    raw_info = account_info.strip()
    if not raw_info:
        return raw_info, False, "Empty line"
    uid = raw_info.split('|')[0].strip()
    params = {'redirect': 'false'}
    try:
        response = requests.get(
            f'https://graph.facebook.com/{uid}/picture',
            params=params,
            headers=CHECK_HEADERS,
            timeout=5
        )
        if response.status_code == 200:
            data = response.json().get('data', {})
            url = data.get('url', '')
            if 'scontent' in url and 'height' in data:
                return raw_info, True, "LIVE"
            else:
                return raw_info, False, "DIE"
        else:
            return raw_info, False, f"Status code: {response.status_code}"
    except Exception as e:
        return raw_info, False, str(e)

def filter_live_accounts(accounts, max_workers=10):
    unique_accounts = []
    seen_uids = set()
    duplicate_accs = []

    for acc in accounts:
        acc_str = acc.strip()
        if not acc_str:
            continue
        uid = acc_str.split('|')[0].strip()
        if uid in seen_uids:
            duplicate_accs.append(acc_str)
        else:
            seen_uids.add(uid)
            unique_accounts.append(acc_str)

    live_accs = []
    die_accs = duplicate_accs

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_acc = {executor.submit(check_single_uid, acc): acc for acc in unique_accounts}
        for future in as_completed(future_to_acc):
            acc_info, is_live, status_msg = future.result()
            if is_live:
                live_accs.append(acc_info)
            else:
                die_accs.append(acc_info)

    return live_accs, die_accs

# ==================== KHỞI TẠO DATABASE ====================
async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    balance BIGINT DEFAULT 0,
                    free_pages_used INT DEFAULT 0
                )
            ''')
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS free_pages_used INT DEFAULT 0")

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    cat_code TEXT PRIMARY KEY,
                    cat_name TEXT NOT NULL,
                    price BIGINT NOT NULL,
                    format_desc TEXT DEFAULT 'UID | Pass | 2FA'
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS stock (
                    id SERIAL PRIMARY KEY,
                    cat_code TEXT NOT NULL,
                    account_info TEXT NOT NULL
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    sepay_id BIGINT PRIMARY KEY,
                    user_id BIGINT,
                    amount BIGINT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS keys (
                    key_code TEXT PRIMARY KEY,
                    duration_days INT NOT NULL,
                    is_used BOOLEAN DEFAULT FALSE,
                    used_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    expired_at TIMESTAMPTZ
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS licenses (
                    license_key TEXT PRIMARY KEY,
                    app_code TEXT NOT NULL DEFAULT 'NVC_TTC_FACEBOOK_MANAGER',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    duration_days INT NOT NULL DEFAULT 0,
                    expires_at TIMESTAMPTZ NULL,
                    machine_id TEXT NULL,
                    activated_at TIMESTAMPTZ NULL,
                    last_seen_at TIMESTAMPTZ NULL,
                    owner_user_id BIGINT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            ''')
            await conn.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS duration_days INT NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS owner_user_id BIGINT NULL")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_licenses_machine ON licenses(machine_id)")

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS page_jobs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    total_tokens INT NOT NULL,
                    max_per_token INT NOT NULL,
                    name_style TEXT NOT NULL,
                    delay_ok INT DEFAULT 300,
                    delay_fail INT DEFAULT 60,
                    ok_count INT DEFAULT 0,
                    fail_count INT DEFAULT 0,
                    free_used INT DEFAULT 0,
                    paid_count INT DEFAULT 0,
                    cost BIGINT DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            await conn.execute("ALTER TABLE page_jobs ADD COLUMN IF NOT EXISTS delay_ok INT DEFAULT 300")
            await conn.execute("ALTER TABLE page_jobs ADD COLUMN IF NOT EXISTS delay_fail INT DEFAULT 60")
            await conn.execute("ALTER TABLE page_jobs ADD COLUMN IF NOT EXISTS free_used INT DEFAULT 0")
            await conn.execute("ALTER TABLE page_jobs ADD COLUMN IF NOT EXISTS paid_count INT DEFAULT 0")

            await conn.execute("""
                INSERT INTO licenses (license_key, app_code, active, duration_days, owner_user_id, created_at)
                SELECT k.key_code, 'NVC_TTC_FACEBOOK_MANAGER', NOT COALESCE(k.is_used, FALSE), k.duration_days, k.used_by::bigint, k.created_at
                FROM keys k
                WHERE k.used_by IS NOT NULL AND k.used_by != ''
                AND NOT EXISTS (
                    SELECT 1 FROM licenses l WHERE l.license_key = k.key_code
                )
            """)
        logging.info("Kết nối và khởi tạo cơ sở dữ liệu PostgreSQL thành công!")
    except Exception as e:
        logging.error(f"Lỗi kết nối PostgreSQL: {e}")
        raise e

# --- CÁC HÀM XỬ LÝ DATABASE ---
async def get_user_balance(user_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT balance FROM users WHERE user_id = $1', user_id)
        if not row:
            await conn.execute('INSERT INTO users (user_id, balance, free_pages_used) VALUES ($1, 0, 0)', user_id)
            return 0
        return row['balance']

async def update_balance(user_id, amount):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET balance = balance + $1 WHERE user_id = $2', amount, user_id)

async def get_user_free_pages_used(user_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT free_pages_used FROM users WHERE user_id = $1', user_id)
        if not row:
            await conn.execute('INSERT INTO users (user_id, balance, free_pages_used) VALUES ($1, 0, 0)', user_id)
            return 0
        return row['free_pages_used'] or 0

async def add_user_free_pages_used(user_id, count):
    async with db_pool.acquire() as conn:
        await conn.execute(
            'UPDATE users SET free_pages_used = COALESCE(free_pages_used, 0) + $1 WHERE user_id = $2',
            count, user_id
        )

async def reset_user_free_pages(user_id):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET free_pages_used = 0 WHERE user_id = $1', user_id)

async def get_all_categories():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT cat_code, cat_name, price, format_desc FROM categories')
        return rows

async def get_stock_count(cat_code):
    async with db_pool.acquire() as conn:
        count = await conn.fetchval('SELECT COUNT(*) FROM stock WHERE cat_code = $1', cat_code)
        return count

async def buy_multiple_accounts_from_stock(cat_code, quantity):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT id, account_info FROM stock WHERE cat_code = $1 LIMIT $2', cat_code, quantity)
        if not rows:
            return []
        acc_ids = [row['id'] for row in rows]
        acc_infos = [row['account_info'] for row in rows]
        await conn.execute('DELETE FROM stock WHERE id = ANY($1::int[])', acc_ids)
        return acc_infos

async def save_page_job(user_id, total_tokens, max_per_token, name_style, delay_ok, delay_fail,
                        ok_count, fail_count, free_used, paid_count, cost):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                '''INSERT INTO page_jobs
                   (user_id, total_tokens, max_per_token, name_style, delay_ok, delay_fail,
                    ok_count, fail_count, free_used, paid_count, cost)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)''',
                user_id, total_tokens, max_per_token, name_style, delay_ok, delay_fail,
                ok_count, fail_count, free_used, paid_count, cost
            )
    except Exception as e:
        logging.error(f"Lỗi lưu page_job: {e}")

def generate_key_string():
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    part3 = ''.join(random.choices(chars, k=4))
    return f"ACP-{part1}-{part2}-{part3}"

# ==================== PHÂN LOẠI FILE UPLOAD ====================
def get_category_info_by_filename(filename):
    fname = filename.upper()
    if "TUT" in fname and "TRAU" in fname:
        return ("cat_tut_trau", "CLONE NGÂM TRÂU - AVT - NAME THÁI - HOTMAIL - AVT - CHẠY JOBS - LIVE ALL 100%", 2500, "UID | Pass | Hotmail | Pass Hotmail | Cookie | Token")
    elif "FIX" in fname or "VIET" in fname:
        return ("cat_fix_viet", "CLONE CHUẨN NAME VIỆT - VER HOTMAIL - AVT - CHẠY JOBS - LIVE ALL 100%", 2800, "UID | Pass | Cookie|Token|Hotmail | Pass Hotmail ")
    elif "BM" in fname:
        return ("cat_bm", "Clone New đã qua BM", 2500, "Hàng login qua cookies, ae log id pass tets trước khi dùng")
    elif "NEWZIN" in fname:
        return ("cat_new_zin", "Clone 6159 - ON2FA -  NAME NGOẠI IP VIỆT - NEW ZIN ALL(Nên mua ít test kỹ trước khi mua SLL)", 1500, "UID | Pass | 2FA | Cookie | Token |MAIL ẢO")
    elif "PAGE" in fname or "KEPPAGE" in fname:
        return ("cat_acc_kep_page", "CLONE KẸP PAGE NAME VIỆT RANDOM 15 - ON2FA - AVT-BÌA (RANDOM) ", 15000, "UID | Pass | 2FA | Cookie | Token |MAIL|PASSMAIL(Nếu có)")
    elif "TRUST" in fname or "2FA" in fname:
        return ("cat_fb_2fa_trust", "CLONE NGÂM TRÂU - NAME RANDOM - ON2FA, NO AVT - HOTMAIL TRUST ", 3000, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU| Hotmail | Pass Hotmail")
    elif "HOTMAIL" in fname or "OUTLOOK" in fname:
        return ("cat_hot_trust", "HOTMAIL-OUTLOOK TRUST 6-12TH CÓ MAILKP ", 350, "MAIL|PASS|RERFESH|CLIENT|MAILKP")
    elif "GMAIL" in fname:
        return ("cat_gmail", "CLONE NGÂM TRÂU - NAME VIỆT - ON2FA - AVT+BÌA - VER GMAIL ", 3500, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU|MAIL")
    elif "NUOI" in fname:
            return ("cat_nuoi", "CLONE VIỆT NUÔI NHIỀU BB - NAME+IP VIỆT - ON2FA - AVT+BÌA - VER GMAIL(random có add mail ảo lấy code) ", 10000, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU|MAIL")
    elif "PHONE" in fname:
        return ("cat_phone", "CLONE NEW - NAME VIỆT - ON2FA - AVT - 3 POST - VER PHONE ", 2500, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU")
    elif "DIEMAIL" in fname:
        return ("cat_die_mail", "CLONE NGÂM - NAME VIỆT - ON2FA - AVT ADD 20 -50 BB - VER HOTMAIL - MAIL DIE ", 3500, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU(MAIL DIE)")
    else:
        return ("cat_new_zin_ngam", "CLONE NGÂM TRÂU - NAME RANDOM - VER HOTMAIL - LIVE ALL 100%", 2000, "UID | PASS | HOTMAIL| COOKIE|TOKEN EAAAAU")

# ═══════════════════════════════════════════════════════════════════
# FB PAGE CREATOR ENGINE
# ═══════════════════════════════════════════════════════════════════
_PAGE_DEVICES = [
    ("samsung", "SM-X910N",  1080, 1920, 3.0,   "13", "33", "MobiFone",    "45201"),
    ("samsung", "SM-S918B",  1080, 2340, 3.0,   "14", "34", "Viettel",     "45204"),
    ("samsung", "SM-A546E",  1080, 2340, 2.75,  "13", "33", "Vinaphone",   "45202"),
    ("Xiaomi",  "2201123G",  1080, 2400, 2.75,  "13", "33", "Viettel",     "45204"),
    ("Xiaomi",  "M2101K7AG", 1080, 2400, 2.75,  "12", "31", "MobiFone",    "45201"),
    ("OPPO",    "CPH2451",   1080, 2412, 3.0,   "14", "34", "Vinaphone",   "45202"),
    ("vivo",    "V2141",     1080, 2400, 2.75,  "13", "33", "Vietnamobile","45205"),
    ("Google",  "Pixel 7",   1080, 2400, 2.625, "14", "34", "Viettel",     "45204"),
    ("Google",  "Pixel 6a",  1080, 2400, 2.625, "13", "33", "MobiFone",    "45201"),
    ("realme",  "RMX3630",   1080, 2412, 3.0,   "13", "33", "Gmobile",     "45207"),
]
_PAGE_FB_VERSIONS = [
    ("450.0.0.42.110", "563890684"),
    ("452.0.0.38.111", "570211455"),
    ("448.0.0.33.120", "559012734"),
    ("455.0.0.25.100", "581234001"),
]
_PAGE_CONN_TYPES = ["WIFI", "WIFI", "WIFI", "MOBILE"]

_PAGE_HO  = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan",
             "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương",
             "Lý", "Đinh", "Trịnh", "Mai", "Tô", "Lương", "Tạ"]
_PAGE_DEM = ["Văn", "Thị", "Minh", "Hoàng", "Thanh", "Quốc", "Hữu",
             "Đức", "Ngọc", "Kim", "Bảo", "Gia", "Khánh", "Tuấn",
             "Anh", "Phương", "Mỹ", "Diệu", "Thùy", "Hồng", ""]
_PAGE_TEN = ["Long", "Hùng", "An", "Bình", "Cường", "Dũng", "Đạt", "Hải",
             "Hiếu", "Hòa", "Huy", "Khánh", "Kiên", "Lâm", "Linh", "Mai",
             "Nam", "Nghĩa", "Nguyên", "Nhân", "Phát", "Phong", "Phúc",
             "Quân", "Quang", "Sơn", "Tài", "Tâm", "Thắng", "Thành",
             "Thiện", "Thịnh", "Thuận", "Tiến", "Toàn", "Trí", "Trung",
             "Tú", "Tuấn", "Tùng", "Vinh", "Vũ", "Yến", "Nhi", "My",
             "Trang", "Hân", "Thư", "Vy", "Nhung", "Ngân", "Loan",
             "Hương", "Lan", "Đào", "Quỳnh", "Trâm", "Anh", "Chi"]
_PAGE_US_FIRST = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard",
    "Joseph", "Thomas", "Charles", "Christopher", "Daniel", "Matthew",
    "Anthony", "Mark", "Paul", "Steven", "Andrew", "Joshua", "Kevin",
    "Brian", "Edward", "Ryan", "Jacob", "Gary", "Eric", "Stephen",
    "Scott", "Brandon", "Benjamin", "Samuel", "Gregory", "Frank",
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara",
    "Susan", "Jessica", "Sarah", "Karen", "Nancy", "Lisa", "Betty",
    "Margaret", "Sandra", "Ashley", "Kimberly", "Emily", "Michelle",
    "Amanda", "Melissa", "Rebecca", "Laura", "Amy", "Angela", "Anna",
]
_PAGE_US_LAST = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson",
    "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez",
    "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen",
    "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell",
]

def _gen_page_fp():
    brand, model, w, h, d, av, sdk, carrier, hni = random.choice(_PAGE_DEVICES)
    fb_ver, fb_bv = random.choice(_PAGE_FB_VERSIONS)
    conn = random.choice(_PAGE_CONN_TYPES)
    conn_token = _uuid.uuid4().hex
    return {
        "ua": (f"[FBAN/FB4A;FBAV/{fb_ver};FBBV/{fb_bv};"
               f"FBDM/{{density={d},width={w},height={h}}};FBLC/vi_VN;FBRV/0;"
               f"FBCR/{carrier};FBMF/{brand.lower()};FBBD/{brand.lower()};"
               f"FBPN/com.facebook.katana;FBDV/{model};FBSV/{av};FBOP/1;"
               f"FBCA/x86_64:arm64-v8a;]"),
        "conn_type": conn,
        "conn_token": conn_token,
        "session_id": f"nid={_uuid.uuid4().hex[:8]};tid=301;nc=0;fc=0;bc=0;cid={conn_token}",
        "device_group": str(random.randint(1000, 9999)),
        "hni": hni,
        "privacy": str(random.randint(10**15, 10**16 - 1)),
        "trace": str(_uuid.uuid4()),
        "brand": brand, "model": model, "carrier": carrier,
    }

def _rand_page_name(style='vn'):
    if style == 'us':
        first = random.choice(_PAGE_US_FIRST)
        last  = random.choice(_PAGE_US_LAST)
        if random.random() < 0.3:
            mid = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            return f"{first} {mid}. {last}"
        return f"{first} {last}"
    ho  = random.choice(_PAGE_HO)
    dem = random.choice(_PAGE_DEM)
    ten = random.choice(_PAGE_TEN)
    return f"{ho} {dem} {ten}" if dem else f"{ho} {ten}"

def _check_token_live(token):
    try:
        r = requests.get(
            f"https://graph.facebook.com/me?fields=id,name&access_token={token}",
            timeout=15,
        )
        j = r.json()
        if "id" in j:
            return j["id"], j.get("name", "?")
    except Exception:
        pass
    return None, None

def _create_page_sync(access_token, page_name, fingerprint):
    url = "https://graph-fallback.facebook.com/graphql"
    BLOKS   = "f5aecaf4351fddd477cdd2e6caa0dd80aa1ac9dd7027df540897caabc146affd"
    APP     = "com.bloks.www.additional.profile.plus.creation.action.category.submit"
    STYLES  = "8e57d61b340c672f5a82cd4d3d821e24"
    FRIENDLY= "FbBloksActionRootQuery-" + APP

    inner = {
        "client_input_params": {
            "entrypoint_logging_data": {
                "fb_profile_editing_session_entry_point": None,
                "field_edit_screen_entry_point": None,
            },
            "profile_plus_id": "0",
            "cp_upsell_declined": 0,
            "off_platform_creator_reachout_id": "",
            "session_id": None,
            "category_ids": ["2347428775505624"],
            "nav_chain": "com.bloks.www.additional.profile.plus.creation,com.bloks.www.additional.profile.plus.creation,,1791748816.167,19071341,,,;",
        },
        "server_params": {
            "referrer": "bottom_sheet_switcher",
            "INTERNAL__latency_qpl_marker_id": 36707139,
            "creation_source": "android",
            "name": page_name,
            "variant": 8,
            "screen": "category",
            "INTERNAL__latency_qpl_instance_id": 54323465500063,
        },
    }
    variables = {
        "params": {
            "params": json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
            "bloks_versioning_id": BLOKS,
            "app_id": APP,
        },
        "scale": "3",
        "nt_context": {
            "using_white_navbar": True, "pixel_ratio": 3, "is_push_on": True,
            "styles_id": STYLES, "bloks_version": BLOKS,
        },
    }
    data = {
        "method": "post", "pretty": "false", "format": "json",
        "server_timestamps": "true", "locale": "vi_VN", "purpose": "fetch",
        "fb_api_req_friendly_name": FRIENDLY,
        "fb_api_caller_class": "graphservice",
        "client_doc_id": "1199408042437361526250104613",
        "variables": json.dumps(variables, ensure_ascii=False, separators=(",", ":")),
        "fb_api_analytics_tags": '["GraphServices"]',
        "client_trace_id": fingerprint["trace"],
    }
    headers = {
        "Authorization": f"OAuth {access_token}",
        "User-Agent": fingerprint["ua"],
        "X-FB-Friendly-Name": FRIENDLY,
        "X-FB-HTTP-Engine": "Liger",
        "X-FB-Connection-Type": fingerprint["conn_type"],
        "x-fb-connection-token": fingerprint["conn_token"],
        "x-fb-session-id": fingerprint["session_id"],
        "x-fb-device-group": fingerprint["device_group"],
        "x-fb-client-ip": "True",
        "x-fb-sim-hni": fingerprint["hni"],
        "x-fb-net-hni": fingerprint["hni"],
        "x-fb-privacy-context": fingerprint["privacy"],
        "x-fb-ta-logging-ids": f"graphql:{fingerprint['trace']}",
        "x-graphql-client-library": "graphservice",
        "x-graphql-request-purpose": "fetch",
        "x-tigon-is-retry": "False",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        response = requests.post(url, headers=headers, data=data, timeout=12)
    except Exception as e:
        return {"success": False, "error": str(e)[:120]}

    try:
        js = response.json()
    except Exception:
        return {"success": False, "error": "invalid_json"}

    if "errors" in js:
        return {"success": False, "error": str(js["errors"][0].get("message", "unknown"))[:120]}

    page_id = None
    try:
        bundle = js["data"]["fb_bloks_action"]["root_action"]["action"]["action_bundle"]["bloks_bundle_action"]
        m = re.search(r'ADDITIONAL_PROFILE_PLUS_CREATION:profile_plus_id["\\ ,]+(\d{10,20})', bundle)
        if m:
            page_id = m.group(1)
    except (KeyError, TypeError):
        pass

    if page_id:
        return {"success": True, "page_id": page_id, "page_name": page_name}
    return {"success": False, "error": "no_page_id"}

async def _run_page_creation_job(bot_inst, chat_id, tokens, max_per_token, name_style,
                                  delay_ok, delay_fail):
    results = []
    lock    = threading.Lock()
    counter = {"done": 0, "ok": 0, "fail": 0}
    total_planned = len(tokens) * max_per_token
    loop = asyncio.get_running_loop()

    def _worker(token):
        uid, uname = _check_token_live(token)
        if not uid:
            with lock:
                results.append({"token": token, "uid": "-", "ok": False, "err": "token_die"})
                counter["done"] += 1
                counter["fail"] += 1
            return

        for p in range(1, max_per_token + 1):
            page_name = _rand_page_name(name_style)
            fp        = _gen_page_fp()
            r         = _create_page_sync(token, page_name, fp)

            with lock:
                if r.get("success"):
                    results.append({
                        "uid": uid, "uname": uname, "token": token,
                        "page_name": page_name, "page_id": r["page_id"], "ok": True,
                    })
                    counter["ok"] += 1
                else:
                    results.append({
                        "uid": uid, "uname": uname, "token": token,
                        "page_name": page_name, "ok": False, "err": r.get("error", "?"),
                    })
                    counter["fail"] += 1
                counter["done"] += 1

            if p < max_per_token:
                if r.get("success"):
                    delay = delay_ok
                    tag = "thành công"
                else:
                    delay = delay_fail
                    tag = "thất bại"
                logging.info(f"[Token {uid}] Page #{p} {tag} → nghỉ {delay}s")
                time.sleep(delay)

    sem = asyncio.Semaphore(PAGE_CREATE_THREADS)

    async def _wrap(token):
        async with sem:
            await loop.run_in_executor(None, _worker, token)

    tasks = [asyncio.create_task(_wrap(tk)) for tk in tokens]

    async def _progress_reporter():
        last = -1
        while True:
            await asyncio.sleep(30)
            with lock:
                done = counter["done"]; ok = counter["ok"]; fail = counter["fail"]
            if done != last:
                last = done
                try:
                    await bot_inst.send_message(
                        chat_id,
                        f"⏳ **Tiến độ tạo page:**\n"
                        f"• Đã xử lý: `{done}/{total_planned}`\n"
                        f"• ✅ Thành công: `{ok}`\n"
                        f"• ❌ Thất bại: `{fail}`\n\n"
                        f"💡 *Nghỉ sau OK: `{delay_ok}s` — sau fail: `{delay_fail}s`*",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
            if all(t.done() for t in tasks):
                break

    reporter = asyncio.create_task(_progress_reporter())
    await asyncio.gather(*tasks)
    reporter.cancel()

    return results, counter

async def _page_creation_task_wrapper(msg_target, user_id, tokens, max_pages, style,
                                       delay_ok, delay_fail):
    try:
        results, counter = await _run_page_creation_job(
            bot, msg_target.chat.id, tokens, max_pages, style, delay_ok, delay_fail
        )
    except Exception as e:
        logging.error(f"Lỗi page creation job: {e}", exc_info=True)
        try:
            await msg_target.answer(f"❌ Lỗi khi chạy job: `{e}`", parse_mode="Markdown")
        except Exception:
            pass
        return

    ok_count   = counter["ok"]
    fail_count = counter["fail"]

    # ─── TÍNH FREE VS PAID ───
    free_used_before = await get_user_free_pages_used(user_id)
    free_remaining   = max(0, FREE_PAGES_FOR_NEW_USER - free_used_before)

    free_used_this_job = min(ok_count, free_remaining)
    paid_count         = max(0, ok_count - free_remaining)
    total_cost         = paid_count * PAGE_CREATE_UNIT_PRICE

    if free_used_this_job > 0:
        await add_user_free_pages_used(user_id, free_used_this_job)

    if total_cost > 0:
        await update_balance(user_id, -total_cost)
    new_balance = await get_user_balance(user_id)

    free_remaining_after = max(0, FREE_PAGES_FOR_NEW_USER - (free_used_before + free_used_this_job))

    await save_page_job(
        user_id, len(tokens), max_pages, style, delay_ok, delay_fail,
        ok_count, fail_count, free_used_this_job, paid_count, total_cost
    )

    lines = []
    for r in results:
        if r["ok"]:
            lines.append(f"{r['uid']}|{r['page_name']}|{r['page_id']}")
    file_bytes = ("\n".join(lines) if lines else "Không có page nào tạo thành công").encode("utf-8")
    file_out = BufferedInputFile(file_bytes, filename=f"pages_{user_id}_{ok_count}ok.txt")

    if free_used_this_job > 0:
        free_note = (
            f"🎁 **Page miễn phí (ưu đãi user mới):**\n"
            f"• Dùng lần này: `{free_used_this_job}`\n"
            f"• Còn lại sau job: `{free_remaining_after}/{FREE_PAGES_FOR_NEW_USER}`\n\n"
        )
    else:
        free_note = (
            f"🎁 **Page miễn phí:** đã hết quota ({FREE_PAGES_FOR_NEW_USER}/{FREE_PAGES_FOR_NEW_USER})\n\n"
        )

    summary = (
        f"🎉 **HOÀN TẤT TẠO PAGE**\n\n"
        f"• ✅ Thành công: `{ok_count}` page\n"
        f"• ❌ Thất bại: `{fail_count}` page\n\n"
        f"{free_note}"
        f"• 💰 Đã trừ: `{total_cost:,} VNĐ` ({paid_count} page × {PAGE_CREATE_UNIT_PRICE:,}đ)\n"
        f"• 💳 Số dư còn lại: `{new_balance:,} VNĐ`\n\n"
        f"📄 File kết quả (`UID|Page Name|Page ID`):"
    )
    try:
        await msg_target.answer(summary, parse_mode="Markdown")
        await msg_target.answer_document(document=file_out)
    except Exception as e:
        logging.error(f"Lỗi gửi kết quả page: {e}")

# ==================== LỆNH BOT ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await get_user_balance(user_id)

    # Banner free
    free_used = await get_user_free_pages_used(user_id)
    free_left = max(0, FREE_PAGES_FOR_NEW_USER - free_used)

    if free_left > 0:
        free_banner = (
            f"🎁 **ƯU ĐÃI USER MỚI!**\n"
            f"Bạn đang có **`{free_left}/{FREE_PAGES_FOR_NEW_USER}`** lượt tạo page **MIỄN PHÍ**!\n"
            f"Bấm 🚀 **Tạo Page FB** để dùng ngay.\n\n"
        )
    else:
        free_banner = ""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Mua Tài Khoản", callback_data="buy_menu"),
            InlineKeyboardButton(text="🔑 Mua Key Tool", callback_data="buy_key_menu")
        ],
        [
            InlineKeyboardButton(text="🚀 Tạo Page FB", callback_data="page_create_menu"),
            InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit")
        ],
        [
            InlineKeyboardButton(text="👤 Tài Khoản", callback_data="profile"),
            InlineKeyboardButton(text="🛠️ Hỗ Trợ", callback_data="support")
        ],
    ])

    await message.answer(
        f"🤖 HỆ THỐNG BÁN VIA/CLONE & KEY TOOL TỰ ĐỘNG 24/7\n\n"
        f"👋 Chào mừng bạn đến với shop!\n"
        f"🚀 Chuyên cung cấp tài khoản chất lượng cao, key tool và tạo page tự động.\n\n"
        f"{free_banner}"
        f"🛡️ Chính sách & Lưu ý:\n"
        f"• Tool TTC chạy page token chỉ chạy mỗi page mua key vào bot để dùng {BOT_TELE}.\n"
        f"• Tạo page: `{PAGE_CREATE_UNIT_PRICE:,}đ` / page **THÀNH CÔNG** — page lỗi miễn phí.\n"
        f"Vui lòng chọn chức năng bên dưới:",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "support")
async def support_callback(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")]
    ])
    await call.message.edit_text(
        f"🛠️ **HƯỚNG DẪN HỖ TRỢ**\n\n"
        f"📞 **Liên hệ hỗ trợ trực tiếp:**\n"
        f"- Telegram: `{SUPPORT_TELEGRAM}`\n"
        f"- Zalo: `{SUPPORT_ZALO}`",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await call.answer()

@dp.callback_query(lambda c: c.data == "back_start")
async def back_start_callback(call: CallbackQuery):
    user_id = call.from_user.id
    free_used = await get_user_free_pages_used(user_id)
    free_left = max(0, FREE_PAGES_FOR_NEW_USER - free_used)

    if free_left > 0:
        free_banner = (
            f"🎁 **ƯU ĐÃI USER MỚI!**\n"
            f"Bạn đang có **`{free_left}/{FREE_PAGES_FOR_NEW_USER}`** lượt tạo page **MIỄN PHÍ**!\n\n"
        )
    else:
        free_banner = ""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Mua Tài Khoản", callback_data="buy_menu"),
            InlineKeyboardButton(text="🔑 Mua Key Tool", callback_data="buy_key_menu")
        ],
        [
            InlineKeyboardButton(text="🚀 Tạo Page FB", callback_data="page_create_menu"),
            InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit")
        ],
        [
            InlineKeyboardButton(text="👤 Tài Khoản", callback_data="profile"),
            InlineKeyboardButton(text="🛠️ Hỗ Trợ", callback_data="support")
        ],
    ])
    await call.message.edit_text(
        f"🤖 HỆ THỐNG BÁN VIA/CLONE & KEY TOOL TỰ ĐỘNG 24/7\n\n"
        f"👋 Chào mừng bạn đến với shop!\n"
        f"🚀 Chuyên cung cấp tài khoản chất lượng cao và key tool tương tác chéo.\n\n"
        f"{free_banner}"
        f"Vui lòng chọn chức năng bên dưới:",
        reply_markup=keyboard
    )
    await call.answer()

@dp.callback_query(lambda c: c.data == "profile")
async def profile_callback(call: CallbackQuery):
    user_id = call.from_user.id
    balance = await get_user_balance(user_id)
    free_used = await get_user_free_pages_used(user_id)
    free_left = max(0, FREE_PAGES_FOR_NEW_USER - free_used)

    await call.message.answer(
        f"👤 **Thông tin tài khoản:**\n"
        f"- ID của bạn: `{user_id}`\n"
        f"- Số dư ví: `{balance:,} VNĐ`\n"
        f"- 🎁 Page free còn lại: `{free_left}/{FREE_PAGES_FOR_NEW_USER}`",
        parse_mode="Markdown"
    )
    await call.answer()

@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_callback(call: CallbackQuery):
    user_id = call.from_user.id
    syntax = f"NAP {user_id}"

    # Dùng params để mã hóa đúng dấu cách/ký tự trong nội dung chuyển khoản.
    qr_url = (
        f"https://img.vietqr.io/image/"
        f"{BANK_ID}-{BANK_ACCOUNT}-compact2.png"
    )
    params = {
        "addInfo": syntax,
        "accountName": ACCOUNT_NAME,
    }

    caption = (
        f"💰 **QUÉT MÃ QR ĐỂ NẠP TIỀN TỰ ĐỘNG**\\n\\n"
        f"- Ngân hàng: **MB Bank**\\n"
        f"- STK: `{BANK_ACCOUNT}`\\n"
        f"- Chủ tài khoản: **{ACCOUNT_NAME}**\\n"
        f"- Nội dung chuyển khoản (bắt buộc): `{syntax}`\\n\\n"
        f"⚠️ Quét mã bằng ứng dụng ngân hàng và giữ nguyên nội dung chuyển khoản.\\n"
        f"⚠️ Số tiền nạp tối thiểu: **10.000đ**."
    )

    try:
        timeout = aiohttp.ClientTimeout(total=20, connect=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                qr_url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                content_type = resp.headers.get("Content-Type", "").lower()
                qr_bytes = await resp.read()

                # VietQR phải trả về dữ liệu ảnh; HTTP 200 đôi khi vẫn là trang lỗi.
                is_png = qr_bytes.startswith(b"\x89PNG\r\n\x1a\n")
                is_jpeg = qr_bytes.startswith(b"\xff\xd8\xff")
                if resp.status != 200 or not (is_png or is_jpeg):
                    preview = qr_bytes[:250].decode("utf-8", errors="replace")
                    logging.error(
                        "VietQR lỗi: status=%s content_type=%s body=%r",
                        resp.status, content_type, preview,
                    )
                    raise RuntimeError(
                        f"VietQR trả về dữ liệu không hợp lệ (HTTP {resp.status})"
                    )

                filename = "qr.png" if is_png else "qr.jpg"
                photo = BufferedInputFile(qr_bytes, filename=filename)
                await call.message.answer_photo(
                    photo=photo,
                    caption=caption,
                    parse_mode="Markdown",
                )

    except Exception:
        logging.exception("Không tạo/gửi được mã QR nạp tiền")
        await call.message.answer(
            "⚠️ **Chưa tải được mã QR. Bạn có thể chuyển khoản thủ công:**\\n\\n"
            f"🏦 Ngân hàng: MB Bank\\n"
            f"💳 STK: `{BANK_ACCOUNT}`\\n"
            f"👤 Chủ tài khoản: `{ACCOUNT_NAME}`\\n"
            f"📝 Nội dung: `{syntax}`\\n"
            "💵 Nạp tối thiểu: `10.000đ`\\n\\n"
            "Vui lòng giữ nguyên nội dung chuyển khoản để hệ thống nhận diện.",
            parse_mode="Markdown",
        )
    finally:
        await call.answer()


# ==================== MUA KEY TOOL ====================
@dp.callback_query(lambda c: c.data == "buy_key_menu")
async def buy_key_menu_callback(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Mua gói 1 Ngày (1,000đ)", callback_data="buykey_1")],
        [InlineKeyboardButton(text="🔑 Mua gói 7 Ngày (7,000đ)", callback_data="buykey_7")],
        [InlineKeyboardButton(text="🔑 Mua gói 30 Ngày (30,000đ)", callback_data="buykey_30")],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")]
    ])
    await call.message.edit_text(
        f"🔑 **HỆ THỐNG MUA KEY TOOL TTC TỰ ĐỘNG**\n\n"
        f"• Tỷ giá: `1,000 VNĐ = 1 Ngày sử dụng`\n"
        f"• Lưu ý: Tool TTC chạy page token chỉ chạy mỗi page mua key vào bot để dùng {BOT_TELE}.\n"
        f"• Chọn gói thời gian bạn muốn mua bên dưới:",
        reply_markup=keyboard,
    )
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("buykey_"))
async def process_buy_key(call: CallbackQuery):
    days = int(call.data.replace("buykey_", ""))
    price = days * 1000
    user_id = int(call.from_user.id)

    balance = await get_user_balance(user_id)
    if balance < price:
        await call.answer(f"❌ Số dư không đủ! Bạn cần {price:,}đ nhưng ví chỉ có {balance:,}đ.", show_alert=True)
        return

    new_key = generate_key_string()
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('UPDATE users SET balance = balance - $1 WHERE user_id = $2', price, user_id)
            await conn.execute(
                '''INSERT INTO licenses
                   (license_key, app_code, active, duration_days, owner_user_id)
                   VALUES ($1, $2, TRUE, $3, $4::bigint)''',
                new_key, LICENSE_APP_CODE, days, user_id
            )

    new_balance = await get_user_balance(user_id)
    await call.message.edit_text(
        f"✅ **Mua key thành công!**\n\n"
        f"🔑 Key của bạn: `{new_key}`\n"
        f"⏳ Thời hạn: `{days} ngày`\n"
        f"💵 Đã trừ: `{price:,} VNĐ`\n"
        f"💰 Số dư còn lại: `{new_balance:,} VNĐ`\n\n"
        f"👉 *Mang key này vào bot {BOT_TELE} để kích hoạt sử dụng!*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")]
        ]),
        parse_mode="Markdown"
    )
    await call.answer()

# ==================== MUA ACC ====================
@dp.callback_query(lambda c: c.data.startswith("buy_menu"))
async def buy_menu_callback(call: CallbackQuery):
    data_parts = call.data.split("_")
    page = int(data_parts[2]) if len(data_parts) > 2 else 0

    categories = await get_all_categories()
    keyboard_buttons = []

    details_text = "📂 **Chọn loại tài khoản bạn muốn mua:**\n\n"

    if not categories:
        details_text += "⚠️ *Shop chưa cập nhật sản phẩm*"
        keyboard_buttons.append([InlineKeyboardButton(text="⚠️ Shop chưa cập nhật sản phẩm", callback_data="back_start")])
    else:
        ITEMS_PER_PAGE = 5
        total_items = len(categories)
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

        if page >= total_pages:
            page = total_pages - 1
        if page < 0:
            page = 0

        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        current_page_categories = categories[start_idx:end_idx]

        for i, cat in enumerate(current_page_categories, start=start_idx + 1):
            cat_code, cat_name, price, format_desc = cat['cat_code'], cat['cat_name'], cat['price'], cat['format_desc']
            count = await get_stock_count(cat_code)

            details_text += f"🔹 **{i}.** `{cat_name}`\n    💰 Giá: `{price:,}đ` | 📦 Còn: `{count}`\n\n"

            short_name = cat_name[:30] + "..." if len(cat_name) > 30 else cat_name
            btn_text = f"{i}. {short_name} ({price:,}đ)"
            keyboard_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"buy_{cat_code}")])

        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(InlineKeyboardButton(text="◀️ Trang trước", callback_data=f"buy_menu_{page - 1}"))

        pagination_buttons.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="ignore"))

        if page < total_pages - 1:
            pagination_buttons.append(InlineKeyboardButton(text="Trang sau ▶️", callback_data=f"buy_menu_{page + 1}"))

        if pagination_buttons:
            keyboard_buttons.append(pagination_buttons)

    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    details_text += "👇 *Nhấn vào nút tương ứng bên dưới để mua:*"

    try:
        await call.message.edit_text(details_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        pass
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("buy_") and not c.data.startswith("buy_key") and not c.data.startswith("buy_menu"))
async def process_buy_category(call: CallbackQuery, state: FSMContext):
    cat_code = call.data.replace("buy_", "")
    stock_count = await get_stock_count(cat_code)

    if stock_count == 0:
        await call.answer("❌ Loại này tạm hết hàng, vui lòng chọn loại khác!", show_alert=True)
        return

    async with db_pool.acquire() as conn:
        cat_info = await conn.fetchrow('SELECT cat_name, price, format_desc FROM categories WHERE cat_code = $1', cat_code)

    cat_name, price, format_desc = cat_info['cat_name'], cat_info['price'], cat_info['format_desc']

    await state.update_data(cat_code=cat_code, cat_name=cat_name, price=price, stock_count=stock_count)
    await state.set_state(BuyState.waiting_for_quantity)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data="qty_1"),
            InlineKeyboardButton(text="5", callback_data="qty_5"),
            InlineKeyboardButton(text="10", callback_data="qty_10")
        ],
        [
            InlineKeyboardButton(text="50", callback_data="qty_50"),
            InlineKeyboardButton(text="100", callback_data="qty_100")
        ],
        [
            InlineKeyboardButton(text="⬅️ Quay lại", callback_data="buy_menu")
        ]
    ])

    await call.message.edit_text(
        f"📦 **{cat_name}**\n\n"
        f"💵 Giá: `{price:,}đ / 1`\n"
        f"📋 Định dạng: `{format_desc}`\n"
        f"📊 Kho hiện còn: `{stock_count}` con\n\n"
        f"👉 **Vui lòng nhập số lượng muốn mua** (hoặc chọn nút nhanh bên dưới):",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("qty_"))
async def process_quick_quantity(call: CallbackQuery, state: FSMContext):
    quantity = int(call.data.replace("qty_", ""))
    await finalize_purchase(call.message, call.from_user.id, quantity, state)
    await call.answer()

@dp.message(BuyState.waiting_for_quantity)
async def process_typed_quantity(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.reply("⚠️ Vui lòng nhập một con số hợp lệ (ví dụ: `5`, `10`)!", parse_mode="Markdown")
        return
    quantity = int(message.text)
    await finalize_purchase(message, message.from_user.id, quantity, state)

async def finalize_purchase(message_target, user_id, quantity, state: FSMContext):
    data = await state.get_data()
    cat_code = data.get("cat_code")
    cat_name = data.get("cat_name")
    price = data.get("price")
    stock_count = data.get("stock_count")

    if quantity <= 0:
        await message_target.answer("⚠️ Số lượng mua phải lớn hơn 0!")
        return

    if quantity > stock_count:
        await message_target.answer(f"❌ Kho không đủ số lượng bạn yêu cầu! Hiện chỉ còn `{stock_count}` con.", parse_mode="Markdown")
        return

    total_price = price * quantity
    balance = await get_user_balance(user_id)

    if balance < total_price:
        await message_target.answer(f"❌ Số dư không đủ! Cần `{total_price:,} VNĐ` nhưng ví của bạn chỉ có `{balance:,} VNĐ`.", parse_mode="Markdown")
        await state.clear()
        return

    await update_balance(user_id, -total_price)
    accounts = await buy_multiple_accounts_from_stock(cat_code, quantity)

    status_msg = await message_target.answer("🔄 **Đang lọc trùng UID & kiểm tra trực tiếp trạng thái Live...**", parse_mode="Markdown")

    loop = asyncio.get_running_loop()
    live_accounts, invalid_accounts = await loop.run_in_executor(None, filter_live_accounts, accounts, 10)

    live_qty = len(live_accounts)
    invalid_qty = len(invalid_accounts)

    if live_qty == 0:
        await update_balance(user_id, total_price)
        await status_msg.edit_text("❌ Tất cả tài khoản trong đợt rút này bị lỗi/DIE/Trùng ID! Bot đã hoàn lại 100% tiền cho bạn.")
        await state.clear()
        return

    refund_amount = invalid_qty * price
    actual_spent = live_qty * price
    if refund_amount > 0:
        await update_balance(user_id, refund_amount)

    new_balance = await get_user_balance(user_id)

    file_content = "\n".join(live_accounts)
    file_bytes = file_content.encode('utf-8')
    txt_file = BufferedInputFile(file_bytes, filename=f"Accounts_LIVE_{live_qty}pcs.txt")

    refund_text = f"\n💸 *Đã hoàn lại:* `{refund_amount:,} VNĐ` (do có {invalid_qty} nick DIE/Trùng UID)" if refund_amount > 0 else ""

    success_text = (
        f"✅ **Giao dịch thành công!**\n"
        f"📦 Loại: `{cat_name}`\n"
        f"🔢 Số lượng Live thực nhận: `{live_qty}` con\n"
        f"💵 Thực thanh toán: `{actual_spent:,} VNĐ`{refund_text}\n"
        f"💰 Số dư ví còn lại: `{new_balance:,} VNĐ`\n\n"
        f"📞 *Hỗ trợ liên hệ Telegram:* `{SUPPORT_TELEGRAM}`\n\n"
        f"📄 *Danh sách tài khoản LIVE đã được đính kèm ở file bên dưới:*"
    )

    await status_msg.delete()
    await message_target.answer(success_text, parse_mode="Markdown")
    await message_target.answer_document(document=txt_file)

    try:
        user_name = "Không rõ"
        username_str = "Không có"
        try:
            chat_member = await bot.get_chat(user_id)
            user_name = chat_member.full_name or "Không rõ"
            if chat_member.username:
                username_str = f"@{chat_member.username}"
        except Exception:
            pass

        vn_time = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        admin_notification = (
            f"🔔 CÓ GIAO DỊCH MUA HÀNG MỚI!\n\n"
            f"👤 Khách hàng: {user_name}\n"
            f"🏷️ Username: {username_str}\n"
            f"🆔 ID: {user_id}\n\n"
            f"📦 Sản phẩm: {cat_name}\n"
            f"🔢 Số lượng giao LIVE: {live_qty}/{quantity}\n"
            f"💵 Tổng tiền nhận: {actual_spent:,} VNĐ\n"
            f"⏱️ Thời gian: {vn_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await bot.send_message(ADMIN_ID, admin_notification)
    except Exception as e:
        logging.error(f"Không thể gửi thông báo mua hàng cho Admin: {e}")

    await state.clear()

# ═══════════════════════════════════════════════════════════════════
# PAGE CREATOR HANDLERS
# ═══════════════════════════════════════════════════════════════════
@dp.callback_query(lambda c: c.data == "page_create_menu")
async def page_create_menu(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    balance = await get_user_balance(user_id)
    free_used = await get_user_free_pages_used(user_id)
    free_left = max(0, FREE_PAGES_FOR_NEW_USER - free_used)

    if free_left > 0:
        free_banner = (
            f"🎁 **ƯU ĐÃI USER MỚI!**\n"
            f"• Bạn còn **`{free_left}/{FREE_PAGES_FOR_NEW_USER}`** page miễn phí\n"
            f"• Page OK đầu tiên sẽ KHÔNG bị trừ tiền (đến hết quota)\n\n"
        )
    else:
        free_banner = f"🎁 Ưu đãi free đã hết ({FREE_PAGES_FOR_NEW_USER}/{FREE_PAGES_FOR_NEW_USER})\n\n"

    text = (
        f"🚀 **TẠO PAGE FACEBOOK TỰ ĐỘNG**\n\n"
        f"{free_banner}"
        f"💰 Giá: `{PAGE_CREATE_UNIT_PRICE:,}đ` / 1 page **THÀNH CÔNG**\n"
        f"🎁 Page lỗi: **miễn phí**\n"
        f"💳 Số dư của bạn: `{balance:,} VNĐ`\n\n"
        f"📋 **Hướng dẫn:**\n"
        f"1️⃣ Chuẩn bị file `.txt` chứa danh sách token (mỗi dòng 1 token)\n"
        f"2️⃣ Bấm nút bên dưới và gửi file\n"
        f"3️⃣ Nhập số page tối đa / token (1–{PAGE_CREATE_MAX_PER_TOKEN})\n"
        f"4️⃣ Chọn kiểu tên (VN / US)\n"
        f"5️⃣ Cấu hình thời gian nghỉ\n"
        f"6️⃣ Xác nhận — bot tự chạy\n\n"
        f"⚠️ *Chỉ tính tiền cho page TẠO THÀNH CÔNG (đã trừ quota free).*"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Gửi file tokens.txt", callback_data="page_upload_start")],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await call.answer()

@dp.callback_query(lambda c: c.data == "page_upload_start")
async def page_upload_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PageCreateState.waiting_for_tokens)
    await call.message.edit_text(
        "📤 **Hãy gửi file `.txt` chứa danh sách token** (mỗi dòng 1 token).\n\n"
        "VD:\n```\nEAAAAU...\nEAAAAU...\n```",
        parse_mode="Markdown",
    )
    await call.answer()

@dp.message(PageCreateState.waiting_for_tokens, F.document)
async def page_receive_tokens(message: types.Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.lower().endswith(".txt"):
        await message.reply("⚠️ Vui lòng gửi file `.txt`!")
        return

    try:
        file_info = await bot.get_file(doc.file_id)
        content   = await bot.download_file(file_info.file_path)
        text      = content.read().decode("utf-8", errors="ignore")
    except Exception as e:
        await message.reply(f"❌ Không đọc được file: {e}")
        return

    tokens = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
    if not tokens:
        await message.reply("❌ File không có token nào hợp lệ!")
        return
    if len(tokens) > PAGE_CREATE_MAX_TOKENS:
        await message.reply(f"❌ Tối đa {PAGE_CREATE_MAX_TOKENS} token / lần. Bạn gửi {len(tokens)}.")
        return

    await state.update_data(tokens=tokens)
    await state.set_state(PageCreateState.waiting_for_max_pages)
    await message.reply(
        f"✅ Đã nhận **{len(tokens)}** token.\n\n"
        f"❓ **Nhập số page tối đa cho mỗi token** (1–{PAGE_CREATE_MAX_PER_TOKEN}):",
        parse_mode="Markdown",
    )

@dp.message(PageCreateState.waiting_for_max_pages)
async def page_receive_max_pages(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.reply(f"⚠️ Vui lòng nhập số (1–{PAGE_CREATE_MAX_PER_TOKEN}).")
        return
    n = int(message.text)
    if n < 1 or n > PAGE_CREATE_MAX_PER_TOKEN:
        await message.reply(f"⚠️ Số page phải từ 1 đến {PAGE_CREATE_MAX_PER_TOKEN}.")
        return

    await state.update_data(max_pages=n)
    await state.set_state(PageCreateState.waiting_for_name_style)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇻🇳 Tên Việt Nam", callback_data="pcs_vn"),
            InlineKeyboardButton(text="🇺🇸 Tên US", callback_data="pcs_us"),
        ],
    ])
    await message.reply("🎭 **Chọn kiểu tên cho page:**", reply_markup=kb)

@dp.callback_query(lambda c: c.data in ("pcs_vn", "pcs_us"), PageCreateState.waiting_for_name_style)
async def page_choose_style(call: CallbackQuery, state: FSMContext):
    style = "vn" if call.data == "pcs_vn" else "us"
    await state.update_data(name_style=style)
    await state.set_state(PageCreateState.waiting_for_delay_choice)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🎯 Dùng mặc định (OK: {PAGE_CREATE_DELAY_SUCCESS // 60} phút | Fail: {PAGE_CREATE_DELAY_FAIL // 60} phút)",
            callback_data="pcs_default_cfg"
        )],
        [InlineKeyboardButton(
            text="⚙️ Tùy chỉnh thời gian nghỉ",
            callback_data="pcs_custom_cfg"
        )],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")],
    ])
    await call.message.edit_text(
        f"⚡ **CẤU HÌNH THỜI GIAN NGHỈ**\n\n"
        f"Chọn cách cấu hình thời gian nghỉ giữa các page:\n\n"
        f"**🎯 Mặc định (khuyến nghị):**\n"
        f"• Page OK → nghỉ `{PAGE_CREATE_DELAY_SUCCESS}s` ({PAGE_CREATE_DELAY_SUCCESS // 60} phút)\n"
        f"• Page fail → nghỉ `{PAGE_CREATE_DELAY_FAIL}s` ({PAGE_CREATE_DELAY_FAIL // 60} phút)\n\n"
        f"**⚙️ Tùy chỉnh:**\n"
        f"• Bạn tự nhập số giây nghỉ sau mỗi page\n"
        f"• Giới hạn: OK từ `{DELAY_OK_MIN}–{DELAY_OK_MAX}s`, Fail từ `{DELAY_FAIL_MIN}–{DELAY_FAIL_MAX}s`",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await call.answer()

# ═══════════════════════════════════════════════════════════════════
# CẤU HÌNH THỜI GIAN NGHỈ
# ═══════════════════════════════════════════════════════════════════
@dp.callback_query(lambda c: c.data == "pcs_default_cfg", PageCreateState.waiting_for_delay_choice)
async def page_default_config(call: CallbackQuery, state: FSMContext):
    await state.update_data(
        delay_ok=PAGE_CREATE_DELAY_SUCCESS,
        delay_fail=PAGE_CREATE_DELAY_FAIL,
        is_custom=False
    )
    data = await state.get_data()
    await _render_confirm(call.message, state, data, is_callback=True)
    await call.answer()

@dp.callback_query(lambda c: c.data == "pcs_custom_cfg", PageCreateState.waiting_for_delay_choice)
async def page_custom_config_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PageCreateState.waiting_for_delay_ok)
    await call.message.edit_text(
        f"⚙️ **TÙY CHỈNH THỜI GIAN NGHỈ**\n\n"
        f"1️⃣ **Nhập số giây nghỉ sau mỗi page THÀNH CÔNG**\n\n"
        f"• Giới hạn: `{DELAY_OK_MIN} – {DELAY_OK_MAX}` giây\n"
        f"• Mặc định khuyến nghị: `{PAGE_CREATE_DELAY_SUCCESS}s` (5 phút)\n"
        f"• VD: `300` = 5 phút, `600` = 10 phút, `900` = 15 phút",
        parse_mode="Markdown"
    )
    await call.answer()

@dp.message(PageCreateState.waiting_for_delay_ok)
async def page_receive_delay_ok(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.reply(f"⚠️ Vui lòng nhập số từ `{DELAY_OK_MIN}` đến `{DELAY_OK_MAX}`.", parse_mode="Markdown")
        return
    n = int(message.text)
    if n < DELAY_OK_MIN or n > DELAY_OK_MAX:
        await message.reply(
            f"⚠️ Ngoài giới hạn! Phải từ `{DELAY_OK_MIN}` đến `{DELAY_OK_MAX}` giây.\n"
            f"VD: 180 = 3 phút, 300 = 5 phút, 900 = 15 phút.",
            parse_mode="Markdown"
        )
        return

    await state.update_data(delay_ok=n)
    await state.set_state(PageCreateState.waiting_for_delay_fail)
    await message.reply(
        f"✅ Đã ghi nhận: **{n}s** nghỉ sau page OK.\n\n"
        f"2️⃣ **Nhập số giây nghỉ sau mỗi page THẤT BẠI**\n\n"
        f"• Giới hạn: `{DELAY_FAIL_MIN} – {DELAY_FAIL_MAX}` giây\n"
        f"• Mặc định khuyến nghị: `{PAGE_CREATE_DELAY_FAIL}s` (1 phút)\n"
        f"• VD: `30` = 30 giây, `120` = 2 phút",
        parse_mode="Markdown"
    )

@dp.message(PageCreateState.waiting_for_delay_fail)
async def page_receive_delay_fail(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.reply(f"⚠️ Vui lòng nhập số từ `{DELAY_FAIL_MIN}` đến `{DELAY_FAIL_MAX}`.", parse_mode="Markdown")
        return
    n = int(message.text)
    if n < DELAY_FAIL_MIN or n > DELAY_FAIL_MAX:
        await message.reply(
            f"⚠️ Ngoài giới hạn! Phải từ `{DELAY_FAIL_MIN}` đến `{DELAY_FAIL_MAX}` giây.\n"
            f"VD: 30 = 30 giây, 60 = 1 phút.",
            parse_mode="Markdown"
        )
        return

    await state.update_data(delay_fail=n, is_custom=True)
    data = await state.get_data()
    await _render_confirm(message, state, data, is_callback=False)

async def _render_confirm(msg_target, state: FSMContext, data: dict, is_callback=False):
    await state.set_state(PageCreateState.waiting_for_name_style)

    tokens     = data["tokens"]
    max_pages  = data["max_pages"]
    style      = data["name_style"]
    delay_ok   = data["delay_ok"]
    delay_fail = data["delay_fail"]

    max_total = len(tokens) * max_pages
    user_id = msg_target.chat.id
    balance = await get_user_balance(user_id)

    free_used = await get_user_free_pages_used(user_id)
    free_left = max(0, FREE_PAGES_FOR_NEW_USER - free_used)

    max_free_usable = min(max_total, free_left)
    max_paid_pages  = max(0, max_total - free_left)
    max_price       = max_paid_pages * PAGE_CREATE_UNIT_PRICE

    label = "Việt Nam" if style == "vn" else "US"

    total_delay_seconds = (max_pages - 1) * delay_ok
    if total_delay_seconds < 60:
        time_est = f"~{total_delay_seconds} giây"
    elif total_delay_seconds < 3600:
        time_est = f"~{total_delay_seconds // 60} phút"
    else:
        h = total_delay_seconds // 3600
        m = (total_delay_seconds % 3600) // 60
        time_est = f"~{h} giờ {m} phút"

    if free_left > 0:
        free_banner = (
            f"🎁 **Ưu đãi free:** `{free_left}/{FREE_PAGES_FOR_NEW_USER}` page\n"
            f"   → Lần này tối đa **`{max_free_usable}`** page được MIỄN PHÍ\n"
            f"   → Vượt quota mới bị trừ tiền\n\n"
        )
    else:
        free_banner = f"🎁 **Ưu đãi free:** đã hết (`{FREE_PAGES_FOR_NEW_USER}/{FREE_PAGES_FOR_NEW_USER}`)\n\n"

    text = (
        f"📋 **XÁC NHẬN TẠO PAGE**\n\n"
        f"• Số token: `{len(tokens)}`\n"
        f"• Page tối đa / token: `{max_pages}`\n"
        f"• Tổng page tối đa: `{max_total}`\n"
        f"• Kiểu tên: **{label}**\n"
        f"• ⏱️ Nghỉ sau OK: `{delay_ok}s`\n"
        f"• ⏱️ Nghỉ sau fail: `{delay_fail}s`\n\n"
        f"{free_banner}"
        f"💰 Giá page vượt quota: `{PAGE_CREATE_UNIT_PRICE:,}đ/page`\n"
        f"💳 Số dư hiện tại: `{balance:,} VNĐ`\n"
        f"🧾 Tối đa có thể trừ: `{max_price:,} VNĐ`\n\n"
        f"⏱️ **Thời gian ước tính:** {time_est}\n"
        f"*(Các token chạy song song, đây là thời gian của 1 token)*\n\n"
        f"👉 Bấm **Xác nhận** để bắt đầu:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Xác nhận chạy", callback_data="pcs_confirm"),
            InlineKeyboardButton(text="❌ Huỷ", callback_data="pcs_cancel"),
        ],
    ])

    if is_callback:
        await msg_target.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg_target.answer(text, reply_markup=kb, parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════
# XÁC NHẬN / HUỶ
# ═══════════════════════════════════════════════════════════════════
@dp.callback_query(lambda c: c.data == "pcs_confirm", PageCreateState.waiting_for_name_style)
async def page_confirm_cb(call: CallbackQuery, state: FSMContext):
    data      = await state.get_data()
    await state.clear()

    tokens     = data.get("tokens")
    max_pages  = data.get("max_pages")
    style      = data.get("name_style")
    delay_ok   = data.get("delay_ok", PAGE_CREATE_DELAY_SUCCESS)
    delay_fail = data.get("delay_fail", PAGE_CREATE_DELAY_FAIL)
    user_id    = call.from_user.id

    if not tokens or not max_pages or not style:
        await call.message.edit_text("❌ Phiên đã hết hạn. Vui lòng bắt đầu lại.")
        await call.answer()
        return

    balance   = await get_user_balance(user_id)
    free_used = await get_user_free_pages_used(user_id)
    free_left = max(0, FREE_PAGES_FOR_NEW_USER - free_used)

    max_total      = len(tokens) * max_pages
    max_paid_pages = max(0, max_total - free_left)
    max_cost       = max_paid_pages * PAGE_CREATE_UNIT_PRICE

    if balance < max_cost:
        await call.message.edit_text(
            f"❌ Số dư không đủ!\n\n"
            f"• Tổng page tối đa: `{max_total}`\n"
            f"• Page free áp dụng: `{min(max_total, free_left)}`\n"
            f"• Page phải trả: `{max_paid_pages}` × `{PAGE_CREATE_UNIT_PRICE:,}đ`\n"
            f"• Cần tối đa: `{max_cost:,} VNĐ`\n"
            f"• Bạn có: `{balance:,} VNĐ`\n\n"
            f"Vui lòng nạp thêm rồi thử lại.",
            parse_mode="Markdown",
        )
        await call.answer()
        return

    await call.message.edit_text(
        f"⚙️ **Đang khởi động job tạo page...**\n"
        f"• Tokens: `{len(tokens)}`\n"
        f"• Max/token: `{max_pages}`\n"
        f"• Kiểu tên: **{'VN' if style == 'vn' else 'US'}**\n"
        f"• Nghỉ OK/Fail: `{delay_ok}s / {delay_fail}s`\n"
        f"• Free còn lại: `{free_left}/{FREE_PAGES_FOR_NEW_USER}`\n\n"
        f"⏳ Bot sẽ báo tiến độ mỗi ~30s.",
        parse_mode="Markdown",
    )
    await call.answer()

    asyncio.create_task(_page_creation_task_wrapper(
        call.message, user_id, tokens, max_pages, style, delay_ok, delay_fail
    ))

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚀 **Có job tạo page mới**\n"
            f"• User: `{user_id}`\n"
            f"• Tokens: `{len(tokens)}`\n"
            f"• Max/token: `{max_pages}`\n"
            f"• Style: **{'VN' if style == 'vn' else 'US'}**\n"
            f"• Delay OK/Fail: `{delay_ok}s / {delay_fail}s`\n"
            f"• Free còn: `{free_left}/{FREE_PAGES_FOR_NEW_USER}`",
            parse_mode="Markdown",
        )
    except Exception:
        pass

@dp.callback_query(lambda c: c.data == "pcs_cancel")
async def page_cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("❌ Đã huỷ job.")
    except Exception:
        pass
    await call.answer()

# ═══════════════════════════════════════════════════════════════════
# ADMIN: /createpage
# ═══════════════════════════════════════════════════════════════════
@dp.message(Command("createpage"))
async def cmd_createpage_admin(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(PageCreateState.waiting_for_tokens)
    await message.reply("👑 **Admin mode** — gửi file tokens.txt.")

# ═══════════════════════════════════════════════════════════════════
# ADMIN: /resetfree
# ═══════════════════════════════════════════════════════════════════
@dp.message(Command("resetfree"))
async def cmd_resetfree(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply(
            "⚠️ **Sai cú pháp!**\n\n"
            "**Cách dùng:**\n"
            "• `/resetfree [user_id]` — Reset 1 user\n"
            "• `/resetfree all` — Reset TẤT CẢ users\n\n"
            "VD: `/resetfree 7718090377`",
            parse_mode="Markdown"
        )
        return

    target = args[1].strip().lower()

    if target == "all":
        try:
            async with db_pool.acquire() as conn:
                result = await conn.execute('UPDATE users SET free_pages_used = 0')
            # result dạng "UPDATE N"
            count = result.split()[-1] if result else "?"
            await message.reply(
                f"✅ **Đã reset quota free cho TẤT CẢ users!**\n"
                f"• Số user được reset: `{count}`\n"
                f"• Mỗi user có lại `{FREE_PAGES_FOR_NEW_USER}` page miễn phí.",
                parse_mode="Markdown"
            )
        except Exception as e:
            await message.reply(f"❌ Lỗi: {e}")
        return

    if not target.isdigit():
        await message.reply("⚠️ User ID phải là số hoặc `all`.")
        return

    target_user_id = int(target)

    try:
        await get_user_balance(target_user_id)  # đảm bảo user tồn tại
        await reset_user_free_pages(target_user_id)

        await message.reply(
            f"✅ **Đã reset quota free!**\n"
            f"• User: `{target_user_id}`\n"
            f"• Quota mới: `{FREE_PAGES_FOR_NEW_USER}` page miễn phí.",
            parse_mode="Markdown"
        )

        # Thông báo cho user
        try:
            await bot.send_message(
                target_user_id,
                f"🎁 **ADMIN ĐÃ RESET QUOTA FREE CHO BẠN!**\n\n"
                f"• Bạn được cấp lại `{FREE_PAGES_FOR_NEW_USER}` page miễn phí.\n"
                f"• Bấm 🚀 **Tạo Page FB** để sử dụng ngay!",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    except Exception as e:
        await message.reply(f"❌ Lỗi reset: {e}")

# ==================== UPLOAD FILE (ADMIN) ====================
@dp.message(lambda message: message.document is not None, StateFilter(None))
async def handle_document_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()

    document = message.document
    file_name = document.file_name

    if not file_name.endswith('.txt'):
        await message.reply("⚠️ Vui lòng gửi file có định dạng `.txt`!", parse_mode="Markdown")
        return

    cat_code, default_cat_name, default_price, default_format_desc = get_category_info_by_filename(file_name)

    file_info = await bot.get_file(document.file_id)
    file_path = file_info.file_path

    downloaded_file = await bot.download_file(file_path)
    file_content = downloaded_file.read().decode('utf-8', errors='ignore')

    lines = file_content.split('\n')
    added_count = 0

    async with db_pool.acquire() as conn:
        existing_cat = await conn.fetchrow('SELECT cat_name, price, format_desc FROM categories WHERE cat_code = $1', cat_code)

        if not existing_cat:
            await conn.execute(
                'INSERT INTO categories (cat_code, cat_name, price, format_desc) VALUES ($1, $2, $3, $4)',
                cat_code, default_cat_name, default_price, default_format_desc
            )
            current_cat_name = default_cat_name
        else:
            current_cat_name = existing_cat['cat_name']

        for line in lines:
            line = line.strip()
            if line:
                await conn.execute('INSERT INTO stock (cat_code, account_info) VALUES ($1, $2)', cat_code, line)
                added_count += 1

    await message.reply(
        f"📥 **Đã nhập kho thành công!**\n"
        f"- Tên file: `{file_name}`\n"
        f"- Phân loại vào: **{current_cat_name[:30]}...**\n"
        f"- Đã thêm: **{added_count}** tài khoản mới vào kho.",
        parse_mode="Markdown"
    )

@dp.message(Command("addmoney"))
async def cmd_add_money(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("⚠️ Sai cú pháp! Dùng: `/addmoney [user_id] [số_tiền]`")
        return
    try:
        target_user_id, amount = int(args[1]), int(args[2])
        await get_user_balance(target_user_id)
        await update_balance(target_user_id, amount)
        await message.reply(f"✅ Đã cộng `{amount:,} VNĐ` cho user `{target_user_id}`!")
        await bot.send_message(target_user_id, f"🎉 Tài khoản vừa được cộng `{amount:,} VNĐ` từ Admin!")
    except ValueError:
        await message.reply("⚠️ User ID và số tiền phải là số!")

# ==================== WEBHOOK SEPAY ====================
async def sepay_webhook_handler(request):
    try:
        auth_header = request.headers.get("Authorization")
        expected_auth = f"Apikey {SEPAY_API_KEY}"

        if not auth_header or auth_header != expected_auth:
            logging.warning("Cảnh báo: Webhook SePay gọi đến nhưng sai hoặc thiếu API Key!")
            return web.json_response({"success": False, "error": "Unauthorized: Invalid API Key"}, status=401)

        try:
            data = await request.json()
        except Exception as e:
            logging.error(f"Lỗi đọc JSON từ SePay: {e}")
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

        logging.info(f"Nhận được webhook hợp lệ từ SePay: {data}")

        sepay_id = data.get("id") or data.get("transactionId")
        transfer_type = data.get("transferType") or data.get("type")
        content = data.get("code") or data.get("content") or ""
        amount = int(data.get("transferAmount") or data.get("amount") or 0)

        if transfer_type and transfer_type.lower() != "in":
            return web.json_response({"success": True, "message": "Ignored outgoing transfer"})

        match = re.search(r'NAP\s*(\d+)', content, re.IGNORECASE)
        if match and amount > 0:
            user_id = int(match.group(1))
            async with db_pool.acquire() as conn:
                inserted = await conn.fetchval(
                    'INSERT INTO transactions (sepay_id, user_id, amount) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING RETURNING sepay_id',
                    sepay_id, user_id, amount
                )
                if inserted:
                    await update_balance(user_id, amount)
                    new_bal = await get_user_balance(user_id)
                    await bot.send_message(
                        user_id,
                        f"🎉 **NẠP TIỀN THÀNH CÔNG!**\n\n"
                        f"💰 Số tiền: `+{amount:,} VNĐ`\n"
                        f"💵 Số dư mới: `{new_bal:,} VNĐ`\n"
                        f"🆔 Mã GD SePay: `{sepay_id}`",
                        parse_mode="Markdown"
                    )
        return web.json_response({"success": True})
    except Exception as e:
        logging.error(f"Lỗi xử lý SePay Webhook: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def scheduled_notification_task():
    interval = 10 * 3600
    await asyncio.sleep(10)

    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    rows = await conn.fetch('SELECT user_id FROM users')

                    for row in rows:
                        user_id = row['user_id']
                        try:
                            await bot.send_message(
                                user_id,
                                "🔔 **Thông báo định kỳ:**\n"
                                "Shop vẫn hoạt động 24/7. AE cần mua clone, key tool hoặc tạo page tự động giá rẻ cứ ghé shop ủng hộ mình nhé!",
                                parse_mode="Markdown"
                            )
                            await asyncio.sleep(10)
                        except Exception as e:
                            logging.error(f"Không thể gửi tin nhắn cho user {user_id}: {e}")

            logging.info("Đã gửi thông báo định kỳ 10 tiếng cho người dùng.")
        except Exception as e:
            logging.error(f"Lỗi trong task thông báo định kỳ: {e}")

        await asyncio.sleep(interval)

async def keep_alive_task():
    interval = 3 * 60
    print(f"--- Bắt đầu script giữ sống cho: {SELF_URL} ---")
    while True:
        await asyncio.sleep(interval)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(SELF_URL, timeout=10) as response:
                    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
                    if response.status == 200:
                        logging.info(f"[{current_time}] Ping giữ sống thành công! Mã phản hồi: {response.status}")
                    else:
                        logging.warning(f"[{current_time}] Server phản hồi mã lạ khi ping: {response.status}")
        except Exception as e:
            current_time = time.strftime("%Y-%m-%d %H:%M:%S")
            logging.error(f"[{current_time}] Lỗi khi ping giữ sống: {e}")

async def main():
    await init_db()

    app = web.Application()
    app.router.add_post('/api/webhook/sepay', sepay_webhook_handler)
    app.router.add_get('/', lambda request: web.Response(text="Bot is running!"))

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, WEBHOOK_HOST, port)
    await site.start()
    print(f"🌐 Webhook Server đang chạy tại cổng {port}...")

    asyncio.create_task(keep_alive_task())
    asyncio.create_task(scheduled_notification_task())
    print("🤖 Bot Telegram đang khởi động...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
