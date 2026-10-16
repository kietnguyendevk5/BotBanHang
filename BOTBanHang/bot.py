import logging
import asyncio
import re
import os
import time
import requests
import asyncpg
import random
import string
import aiohttp
from concurrent.futures import ThreadPoolExecutor, as_completed
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
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

# Múi giờ Việt Nam
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Headers kiểm tra Live UID
CHECK_HEADERS = {
    'accept': '*/*',
    'accept-language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
    'origin': 'https://timuid.com',
    'referer': 'https://timuid.com/',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

db_pool = None


class BuyState(StatesGroup):
    waiting_for_quantity = State()


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


# ==================== HÀM BÓC TÊN NGƯỜI CHUYỂN ====================
def extract_sender_name(content: str, description: str = "") -> str:
    """
    Cố gắng bóc tên người chuyển từ nội dung CK SePay.
    Ví dụ: 'NGUYEN VAN A chuyen tien NAP 123456' -> 'NGUYEN VAN A'
    """
    text = f"{content or ''} {description or ''}".strip()
    if not text:
        return "Không rõ"

    # Bỏ mã NAP xxxxx
    text = re.sub(r'NAP\s*\d+', '', text, flags=re.IGNORECASE).strip()
    # Bỏ từ khóa ngân hàng
    text = re.sub(r'(chuyen\s*tien|chuyển\s*tiền|ck|transfer|GD)', '', text, flags=re.IGNORECASE).strip()
    # Xóa khoảng trắng dư
    text = re.sub(r'\s+', ' ', text).strip()

    return text[:60] if text else "Không rõ"


# ==================== HÀM GIỜ VIỆT NAM ====================
def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def format_vn_datetime(dt) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(VN_TZ).strftime("%d/%m/%Y %H:%M:%S")


# ==================== KHỞI TẠO DATABASE ====================
async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    balance BIGINT DEFAULT 0
                )
            ''')
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

            # ===== BẢNG RIÊNG LƯU SỐ TIỀN + NGƯỜI CHUYỂN (LISTENER SẼ ĐỌC) =====
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payment_logs (
                    id SERIAL PRIMARY KEY,
                    sepay_id BIGINT UNIQUE,
                    user_id BIGINT,
                    amount BIGINT NOT NULL,
                    sender_name TEXT DEFAULT 'Không rõ',
                    bank_name TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')
                )
            ''')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_payment_logs_id ON payment_logs(id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_payment_logs_time ON payment_logs(created_at DESC)')

            await conn.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS duration_days INT NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS owner_user_id BIGINT NULL")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_licenses_machine ON licenses(machine_id)")

            await conn.execute("""
                INSERT INTO licenses (license_key, app_code, active, duration_days, owner_user_id, created_at)
                SELECT k.key_code, 'NVC_TTC_FACEBOOK_MANAGER', NOT COALESCE(k.is_used, FALSE), k.duration_days, k.used_by::bigint, k.created_at
                FROM keys k
                WHERE k.used_by IS NOT NULL AND k.used_by != ''
                AND NOT EXISTS (
                    SELECT 1 FROM licenses l WHERE l.license_key = k.key_code
                )
            """)
        logging.info("✅ Kết nối và khởi tạo PostgreSQL thành công!")
    except Exception as e:
        logging.error(f"❌ Lỗi kết nối PostgreSQL: {e}")
        raise e


# --- CÁC HÀM XỬ LÝ DATABASE (ASYNC) ---
async def get_user_balance(user_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT balance FROM users WHERE user_id = $1', user_id)
        if not row:
            await conn.execute('INSERT INTO users (user_id, balance) VALUES ($1, 0)', user_id)
            return 0
        return row['balance']


async def update_balance(user_id, amount):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET balance = balance + $1 WHERE user_id = $2', amount, user_id)


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


def generate_key_string():
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    part3 = ''.join(random.choices(chars, k=4))
    return f"ACP-{part1}-{part2}-{part3}"


# ==================== HÀM PHÂN LOẠI DỰA TRÊN TÊN FILE ====================
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
        return ("cat_acc_kep_page", "CLONE KẸP PAGE NAME VIỆT RANDOM 5-10 - ON2FA - AVT-BÌA (RANDOM) ", 6000, "UID | Pass | 2FA | Cookie | Token |MAIL|PASSMAIL(Nếu có)")
    elif "TRUST" in fname or "2FA" in fname:
        return ("cat_fb_2fa_trust", "CLONE NGÂM TRÂU - NAME RANDOM - ON2FA, NO AVT - HOTMAIL TRUST ", 3000, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU| Hotmail | Pass Hotmail")
    elif "HOTMAIL" in fname or "OUTLOOK" in fname:
        return ("cat_hot_trust", "HOTMAIL-OUTLOOK TRUST 6-12TH CÓ MAILKP ", 350, "MAIL|PASS|RERFESH|CLIENT|MAILKP")
    elif "GMAIL" in fname:
        return ("cat_gmail", "CLONE NGÂM TRÂU - NAME RANDOM - ON2FA - NO AVT - VER GMAIL ", 3500, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU|MAIL")
    elif "PHONE" in fname:
        return ("cat_phone", "CLONE NEW - NAME VIỆT - ON2FA - AVT - 3 POST - VER PHONE ", 2500, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU")
    elif "DIEMAIL" in fname:
        return ("cat_die_mail", "CLONE NGÂM - NAME VIỆT - ON2FA - AVT ADD 20 -50 BB - VER HOTMAIL - MAIL DIE ", 3500, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU(MAIL DIE)")
    else:
        return ("cat_new_zin_ngam", "CLONE NGÂM TRÂU - NAME RANDOM - VER HOTMAIL - LIVE ALL 100%", 2000, "UID | PASS | HOTMAIL| COOKIE|TOKEN EAAAAU")


# ==================== CÁC LỆNH BOT ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await get_user_balance(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Mua Tài Khoản", callback_data="buy_menu"),
            InlineKeyboardButton(text="🔑 Mua Key Tool", callback_data="buy_key_menu")
        ],
        [
            InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit"),
            InlineKeyboardButton(text="👤 Tài Khoản", callback_data="profile")
        ],
    ])
    await message.answer(
        f"🤖 HỆ THỐNG BÁN VIA/CLONE & KEY TOOL TỰ ĐỘNG 24/7\n\n"
        f"👋 Chào mừng bạn đến với shop!\n"
        f"🚀 Chuyên cung cấp tài khoản chất lượng cao và key tool tương tác chéo.\n\n"
        f"🛡️ Chính sách & Lưu ý:\n"
        f"• Tool TTC chạy page token chỉ chạy mỗi page mua key vào bot để dùng {BOT_TELE}.\n"
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Mua Tài Khoản", callback_data="buy_menu"),
            InlineKeyboardButton(text="🔑 Mua Key Tool", callback_data="buy_key_menu")
        ],
        [
            InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit"),
            InlineKeyboardButton(text="👤 Tài Khoản", callback_data="profile")
        ],
    ])
    await call.message.edit_text(
        f"🤖 HỆ THỐNG BÁN VIA/CLONE & KEY TOOL TỰ ĐỘNG 24/7\n\n"
        f"👋 Chào mừng bạn đến với shop!\n"
        f"🚀 Chuyên cung cấp tài khoản chất lượng cao và key tool tương tác chéo.\n\n"
        f"Vui lòng chọn chức năng bên dưới:",
        reply_markup=keyboard
    )
    await call.answer()


@dp.callback_query(lambda c: c.data == "profile")
async def profile_callback(call: CallbackQuery):
    user_id = call.from_user.id
    balance = await get_user_balance(user_id)
    await call.message.answer(
        f"👤 **Thông tin tài khoản:**\n"
        f"- ID của bạn: `{user_id}`\n"
        f"- Số dư ví: `{balance:,} VNĐ`",
        parse_mode="Markdown"
    )
    await call.answer()


@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_callback(call: CallbackQuery):
    user_id = call.from_user.id
    syntax = f"NAP {user_id}"
    qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACCOUNT}-compact2.png?addInfo={syntax}&accountName={urllib_quote(ACCOUNT_NAME)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(qr_url) as resp:
                if resp.status == 200:
                    qr_bytes = await resp.read()
                    photo = BufferedInputFile(qr_bytes, filename="qr.png")
                    caption = (
                        f"💰 **QUÉT MÃ QR ĐỂ NẠP TIỀN TỰ ĐỘNG**\n\n"
                        f"- Ngân hàng: **MB Bank**\n"
                        f"- STK: `{BANK_ACCOUNT}`\n"
                        f"- Chủ tên: **{ACCOUNT_NAME}**\n"
                        f"- Nội dung chuyển khoản (Bắt buộc): `{syntax}`\n\n"
                        f"⚠️ *Dùng app ngân hàng quét mã QR để nạp tự động sau vài giây.*\n"
                        f"⚠️ *MIN NẠP 10,000đ.*"
                    )
                    await call.message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")
                else:
                    await call.message.answer("❌ Không thể tạo mã QR lúc này!")
    except Exception as e:
        logging.error(f"Lỗi tải mã QR: {e}")
        await call.message.answer(f"💰 Chuyển khoản thủ công:\n- STK: `{BANK_ACCOUNT}`\n- Nội dung: `{syntax}`\n- MIN NẠP: 10,000đ")
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
        await call.answer(f"❌ Số dư không đủ! Cần {price:,}đ, ví có {balance:,}đ.", show_alert=True)
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
        f"👉 *Mang key này vào bot {BOT_TELE} để kích hoạt!*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")]
        ]),
        parse_mode="Markdown"
    )
    await call.answer()


# ==================== MUA TÀI KHOẢN ====================
@dp.callback_query(lambda c: c.data.startswith("buy_menu"))
async def buy_menu_callback(call: CallbackQuery):
    data_parts = call.data.split("_")
    page = int(data_parts[2]) if len(data_parts) > 2 else 0
    categories = await get_all_categories()
    keyboard_buttons = []
    details_text = "📂 **Chọn loại tài khoản bạn muốn mua:**\n\n"

    if not categories:
        details_text += "⚠️ *Shop chưa cập nhật sản phẩm*"
        keyboard_buttons.append([InlineKeyboardButton(text="⚠️ Chưa có sản phẩm", callback_data="back_start")])
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
            pagination_buttons.append(InlineKeyboardButton(text="◀️ Trước", callback_data=f"buy_menu_{page - 1}"))
        pagination_buttons.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            pagination_buttons.append(InlineKeyboardButton(text="Sau ▶️", callback_data=f"buy_menu_{page + 1}"))
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


@dp.callback_query(lambda c: c.data.startswith("buy_"))
async def process_buy_category(call: CallbackQuery, state: FSMContext):
    cat_code = call.data.replace("buy_", "")
    stock_count = await get_stock_count(cat_code)
    if stock_count == 0:
        await call.answer("❌ Loại này tạm hết hàng!", show_alert=True)
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
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="buy_menu")]
    ])

    await call.message.edit_text(
        f"📦 **{cat_name}**\n\n"
        f"💵 Giá: `{price:,}đ / 1`\n"
        f"📋 Định dạng: `{format_desc}`\n"
        f"📊 Kho hiện còn: `{stock_count}` con\n\n"
        f"👉 **Vui lòng nhập số lượng muốn mua** (hoặc chọn nút nhanh):",
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
        await message.reply("⚠️ Vui lòng nhập số hợp lệ (VD: `5`, `10`)!", parse_mode="Markdown")
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
        await message_target.answer(f"❌ Kho không đủ! Chỉ còn `{stock_count}` con.", parse_mode="Markdown")
        return

    total_price = price * quantity
    balance = await get_user_balance(user_id)
    if balance < total_price:
        await message_target.answer(f"❌ Số dư không đủ! Cần `{total_price:,} VNĐ`, ví có `{balance:,} VNĐ`.", parse_mode="Markdown")
        await state.clear()
        return

    await update_balance(user_id, -total_price)
    accounts = await buy_multiple_accounts_from_stock(cat_code, quantity)
    status_msg = await message_target.answer("🔄 **Đang lọc trùng UID & kiểm tra LIVE...**", parse_mode="Markdown")

    loop = asyncio.get_running_loop()
    live_accounts, invalid_accounts = await loop.run_in_executor(None, filter_live_accounts, accounts, 10)
    live_qty = len(live_accounts)
    invalid_qty = len(invalid_accounts)

    if live_qty == 0:
        await update_balance(user_id, total_price)
        await status_msg.edit_text("❌ Tất cả acc lỗi/DIE/Trùng ID! Đã hoàn 100% tiền.")
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
    refund_text = f"\n💸 *Đã hoàn:* `{refund_amount:,} VNĐ` ({invalid_qty} nick DIE/Trùng)" if refund_amount > 0 else ""

    success_text = (
        f"✅ **Giao dịch thành công!**\n"
        f"📦 Loại: `{cat_name}`\n"
        f"🔢 Số lượng Live: `{live_qty}` con\n"
        f"💵 Thực TT: `{actual_spent:,} VNĐ`{refund_text}\n"
        f"💰 Số dư còn: `{new_balance:,} VNĐ`\n\n"
        f"📞 *Hỗ trợ:* `{SUPPORT_TELEGRAM}`\n\n"
        f"📄 *File tài khoản LIVE đính kèm:*"
    )

    await status_msg.delete()
    await message_target.answer(success_text, parse_mode="Markdown")
    await message_target.answer_document(document=txt_file)

    # Thông báo admin
    try:
        user_name = "Không rõ"
        username_str = "Không có"
        try:
            chat_member = await bot.get_chat(user_id)
            user_name = chat_member.full_name or "Không rõ"
            if chat_member.username:
                username_str = f"@{chat_member.username}"
        except Exception:
            if hasattr(message_target, 'from_user') and message_target.from_user and not message_target.from_user.is_bot:
                user_name = message_target.from_user.full_name or "Không rõ"
                if message_target.from_user.username:
                    username_str = f"@{message_target.from_user.username}"

        admin_notification = (
            f"🔔 CÓ GIAO DỊCH MUA HÀNG MỚI!\n\n"
            f"👤 Khách hàng: {user_name}\n"
            f"🏷️ Username: {username_str}\n"
            f"🆔 ID: {user_id}\n\n"
            f"📦 Sản phẩm: {cat_name}\n"
            f"🔢 SL giao LIVE: {live_qty}/{quantity}\n"
            f"💵 Tổng tiền: {actual_spent:,} VNĐ\n"
            f"⏱️ Thời gian: {format_vn_datetime(now_vn())}"
        )
        await bot.send_message(ADMIN_ID, admin_notification)
    except Exception as e:
        logging.error(f"Không gửi được thông báo admin: {e}")

    await state.clear()


@dp.message(lambda message: message.document is not None)
async def handle_document_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    document = message.document
    file_name = document.file_name
    if not file_name.endswith('.txt'):
        await message.reply("⚠️ Vui lòng gửi file `.txt`!", parse_mode="Markdown")
        return

    cat_code, default_cat_name, default_price, default_format_desc = get_category_info_by_filename(file_name)
    file_info = await bot.get_file(document.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    file_content = downloaded_file.read().decode('utf-8', errors='ignore')
    lines = file_content.split('\n')
    added_count = 0

    async with db_pool.acquire() as conn:
        existing_cat = await conn.fetchrow('SELECT cat_name FROM categories WHERE cat_code = $1', cat_code)
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
        f"📥 **Đã nhập kho!**\n"
        f"- File: `{file_name}`\n"
        f"- Phân loại: **{current_cat_name[:40]}**\n"
        f"- Đã thêm: **{added_count}** acc mới.",
        parse_mode="Markdown"
    )


@dp.message(Command("addmoney"))
async def cmd_add_money(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("⚠️ Cú pháp: `/addmoney [user_id] [số_tiền]`")
        return
    try:
        target_user_id, amount = int(args[1]), int(args[2])
        await get_user_balance(target_user_id)
        await update_balance(target_user_id, amount)
        await message.reply(f"✅ Đã cộng `{amount:,} VNĐ` cho user `{target_user_id}`!")
        await bot.send_message(target_user_id, f"🎉 Tài khoản vừa được cộng `{amount:,} VNĐ`!")
    except ValueError:
        await message.reply("⚠️ User ID và số tiền phải là số!")


@dp.message(Command("logs"))
async def cmd_view_logs(message: types.Message):
    """Admin xem 15 giao dịch gần nhất."""
    if message.from_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            '''SELECT user_id, amount, sender_name, bank_name, content, created_at
               FROM payment_logs ORDER BY created_at DESC LIMIT 15'''
        )
    if not rows:
        await message.reply("📭 Chưa có giao dịch nào.")
        return

    text = "📒 **BẢNG GIAO DỊCH (GIỜ VN)**\n\n"
    for r in rows:
        text += (
            f"🕒 `{format_vn_datetime(r['created_at'])}`\n"
            f"👤 Người chuyển: `{r['sender_name']}`\n"
            f"💰 Số tiền: `{r['amount']:,} VNĐ`\n"
            f"🆔 User ID: `{r['user_id']}`\n"
            f"🏦 NH: `{r['bank_name']}`\n"
            f"📝 ND: `{(r['content'] or '')[:50]}`\n"
            f"──────────────\n"
        )
    await message.reply(text, parse_mode="Markdown")


# ==================== WEBHOOK SEPAY ====================
async def sepay_webhook_handler(request):
    try:
        auth_header = request.headers.get("Authorization")
        expected_auth = f"Apikey {SEPAY_API_KEY}"
        if not auth_header or auth_header != expected_auth:
            logging.warning("Webhook SePay sai API Key!")
            return web.json_response({"success": False, "error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception as e:
            logging.error(f"Lỗi đọc JSON SePay: {e}")
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

        logging.info(f"📥 Webhook SePay: {data}")

        sepay_id      = data.get("id") or data.get("transactionId")
        transfer_type = data.get("transferType") or data.get("type")
        content       = data.get("code") or data.get("content") or ""
        description   = data.get("description") or ""
        amount        = int(data.get("transferAmount") or data.get("amount") or 0)
        bank_name     = data.get("gateway") or data.get("bankName") or ""

        # Chỉ xử lý tiền VÀO
        if transfer_type and transfer_type.lower() != "in":
            return web.json_response({"success": True, "message": "Ignored outgoing"})

        sender_name = extract_sender_name(content, description)

        match = re.search(r'NAP\s*(\d+)', content, re.IGNORECASE)
        if match and amount > 0:
            user_id = int(match.group(1))
            vn_now = now_vn()

            async with db_pool.acquire() as conn:
                inserted = await conn.fetchval(
                    '''INSERT INTO transactions (sepay_id, user_id, amount, created_at)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT DO NOTHING RETURNING sepay_id''',
                    sepay_id, user_id, amount, vn_now
                )

                # 📌 GHI VÀO BẢNG RIÊNG CHO LISTENER ĐỌC
                await conn.execute(
                    '''INSERT INTO payment_logs
                       (sepay_id, user_id, amount, sender_name, bank_name, content, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (sepay_id) DO NOTHING''',
                    sepay_id, user_id, amount, sender_name, bank_name, content, vn_now
                )

                if inserted:
                    await update_balance(user_id, amount)
                    new_bal = await get_user_balance(user_id)
                    await bot.send_message(
                        user_id,
                        f"🎉 **NẠP TIỀN THÀNH CÔNG!**\n\n"
                        f"💰 Số tiền: `+{amount:,} VNĐ`\n"
                        f"👤 Người chuyển: `{sender_name}`\n"
                        f"🏦 Ngân hàng: `{bank_name}`\n"
                        f"💵 Số dư: `{new_bal:,} VNĐ`\n"
                        f"🕒 Thời gian: `{format_vn_datetime(vn_now)}`",
                        parse_mode="Markdown"
                    )

        return web.json_response({"success": True})
    except Exception as e:
        logging.error(f"Lỗi SePay Webhook: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ==================== TASK ĐỊNH KỲ ====================
async def scheduled_notification_task():
    interval = 10 * 3600
    await asyncio.sleep(10)
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    rows = await conn.fetch('SELECT user_id FROM users')
                    for row in rows:
                        try:
                            await bot.send_message(
                                row['user_id'],
                                "🔔 **Thông báo định kỳ:**\nShop hoạt động 24/7. AE cần mua clone/key tool giá rẻ ghé shop ủng hộ nhé!",
                                parse_mode="Markdown"
                            )
                            await asyncio.sleep(10)
                        except Exception as e:
                            logging.error(f"Không gửi được cho {row['user_id']}: {e}")
            logging.info("✅ Đã gửi thông báo định kỳ.")
        except Exception as e:
            logging.error(f"Lỗi task định kỳ: {e}")
        await asyncio.sleep(interval)


async def keep_alive_task():
    interval = 3 * 60
    print(f"--- Keep-alive: {SELF_URL} ---")
    while True:
        await asyncio.sleep(interval)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(SELF_URL, timeout=10) as response:
                    t = time.strftime("%Y-%m-%d %H:%M:%S")
                    if response.status == 200:
                        logging.info(f"[{t}] Ping OK: {response.status}")
                    else:
                        logging.warning(f"[{t}] Ping mã lạ: {response.status}")
        except Exception as e:
            t = time.strftime("%Y-%m-%d %H:%M:%S")
            logging.error(f"[{t}] Lỗi ping: {e}")


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
    print(f"🌐 Webhook Server chạy tại cổng {port}...")

    asyncio.create_task(keep_alive_task())
    asyncio.create_task(scheduled_notification_task())
    print("🤖 Bot Telegram đang khởi động...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
