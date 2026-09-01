import logging
import asyncio
import re
import os
import time
import requests
import asyncpg
import random
import string
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
import aiohttp

# ==================== CẤU HÌNH NGÂN HÀNG & BOT ====================
API_TOKEN = '8735568227:AAFq02ZhIJLfW5ojVg5q3xVYRNeq3AGK9CQ' 
ADMIN_ID = 7718090377         
BANK_ID = "MB"                    # Mã VietQR của MB Bank
BANK_ACCOUNT = "0356442864"       # Số tài khoản
ACCOUNT_NAME = "NGUYEN DIEN TUAN KIET" 
SUPPORT_TELEGRAM = "@kietnguyen0999" # Thay bằng username Telegram của bạn
SUPPORT_ZALO = "0356442864"        # Thay bằng số điện thoại Zalo của bạn
WEBHOOK_HOST = '0.0.0.0'
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "spsk_test_zFCU1AguPj8T7RqzMAMRxSbgaspYi99y")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:fVXjKs8XvC9lljvT@db.xfyfbpqyelrzfsgwhgbc.supabase.co:5432/postgres")
SELF_URL = "https://botbanhang-s6iq.onrender.com/" # Link bot của bạn trên Render
BOT_TELE = "@ToolTtc_bot"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Biến toàn cục chứa kết nối pool database
db_pool = None

# Định nghĩa trạng thái FSM
class BuyState(StatesGroup):
    waiting_for_quantity = State()

# ==================== KHỞI TẠO DATABASE POSTGRESQL ====================
async def init_db():
    global db_pool
    try:
        # Thêm statement_cache_size=0 để tránh lỗi DuplicatePreparedStatementError với PgBouncer
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
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh'),
                    expired_at TIMESTAMP WITH TIME ZONE
                )
            ''')
        logging.info("Kết nối và khởi tạo cơ sở dữ liệu PostgreSQL thành công!")
    except Exception as e:
        logging.error(f"Lỗi kết nối PostgreSQL: {e}")
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
    return f"TTC-{part1}-{part2}-{part3}"

# ==================== HÀM PHÂN LOẠI DỰA TRÊN TÊN FILE ====================
def get_category_info_by_filename(filename):
    fname = filename.upper()
    if "TUT" in fname and "TRAU" in fname:
        return ("cat_tut_trau", "CLONE NGÂM TRÂU - AVT - NAME THÁI - HOTMAIL - AVT - CHẠY JOBS - LIVE ALL 100%", 2500, "UID | Pass | Hotmail | Pass Hotmail | Cookie | Token")
    elif "FIX" in fname or "VIET" in fname:
        return ("cat_fix_viet", "CLONE CHUẨN NAME VIỆT - VER HOTMAIL - AVT - CHẠY JOBS - LIVE ALL 100%", 2500, "UID | Pass | Hotmail | Pass Hotmail | Cookie")
    elif "BM" in fname:
        return ("cat_bm", "Clone New đã qua BM", 2500, "Hàng login qua cookies, ae log id pass tets trước khi dùng")
    elif "TRUST" in fname or "2FA" in fname:
        return ("cat_fb_2fa_trust", "CLONE NGÂM TRÂU - NAME RANDOM - ON2FA, NO AVT ", 3000, "UID | Pass | 2FA |COOKIE|TOKEN EAAAAU| Hotmail | Pass Hotmail")   
    elif "HOTMAIL" in fname or "OUTLOOK" in fname:
        return ("cat_hot_trust", "HOTMAIL-OUTLOOK TRUST 6-12TH CÓ MAILKP ", 350, "MAIL|PASS|RERFESH|CLIENT|MAILKP")    
    else:
        return ("cat_new_zin", "CLONE NGÂM TRÂU - NAME RANDOM - VER HOTMAIL - LIVE ALL 100%", 2000, "UID | PASS | HOTMAIL| COOKIE|TOKEN EAAAAU")

# ==================== CÁC LỆNH CỦA BOT TELEGRAM ====================
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
        [
            InlineKeyboardButton(text="🛠️ Hỗ Trợ & Bảo Hành", callback_data="support")
        ]
    ])
    
    await message.answer(
        f"🤖 HỆ THỐNG BÁN VIA/CLONE & KEY TOOL TỰ ĐỘNG 24/7\n\n"
        f"👋 Chào mừng bạn đến với shop!\n"
        f"🚀 Chuyên cung cấp tài khoản chất lượng cao và key tool tương tác chéo.\n\n"
        f"🛡️ Chính sách & Lưu ý:\n"
        f"• Bảo hành 1 đổi 1 nếu lỗi lần đầu đăng nhập.\n"
        f"• Tool TTC chạy page token chỉ chạy mỗi page mua key vào bot để dùng {BOT_TELE}.\n"
        f"• Bắt buộc: Quay video từ lúc mua đến lúc login để được hỗ trợ.\n\n"
        f"Vui lòng chọn chức năng bên dưới:",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "support")
async def support_callback(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")]
    ])
    await call.message.edit_text(
        f"🛠️ **HƯỚNG DẪN HỖ TRỢ & BẢO HÀNH**\n\n"
        f"🛡️ **Chính sách bảo hành:**\n"
        f"- Bảo hành **1 đổi 1** cho các tài khoản lỗi (Sai pass, die, checkpoint ngay lần đầu đăng nhập trong vòng 24h).\n"
        f"- Yêu cầu: Có video quay lại quá trình mua và check tài khoản hoặc có ảnh thời gian lúc mua.\n\n"
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
        [
            InlineKeyboardButton(text="🛠️ Hỗ Trợ & Bảo Hành", callback_data="support")
        ]
    ])
    await call.message.edit_text(
        f"🤖 HỆ THỐNG BÁN VIA/CLONE & KEY TOOL TỰ ĐỘNG 24/7\n\n"
        f"👋 Chào mừng bạn đến với shop!\n"
        f"🚀 Chuyên cung cấp tài khoản chất lượng cao và key tool tương tác chéo.\n\n"
        f"🛡️ Chính sách & Lưu ý:\n"
        f"• Bảo hành 1 đổi 1 nếu lỗi lần đầu đăng nhập.\n"
        f"• Tool TTC chạy page token chỉ chạy mỗi page mua key vào bot để dùng {BOT_TELE}.\n"
        f"• Bắt buộc: Quay video từ lúc mua đến lúc login để được hỗ trợ.\n\n"
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

def urllib_quote(text):
    import urllib.parse
    return urllib.parse.quote(text)

# ==================== TÍNH NĂNG MUA KEY TOOL ====================
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
    price = days * 1000  # 1k 1 ngày 1 key
    user_id = call.from_user.id

    balance = await get_user_balance(user_id)
    if balance < price:
        await call.answer(f"❌ Số dư không đủ! Bạn cần {price:,}đ nhưng ví chỉ có {balance:,}đ.", show_alert=True)
        return

    new_key = generate_key_string()
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('UPDATE users SET balance = balance - $1 WHERE user_id = $2', price, user_id)
            await conn.execute(
                'INSERT INTO keys (key_code, duration_days, is_used) VALUES ($1, $2, FALSE)',
                new_key, days
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

@dp.callback_query(lambda c: c.data == "buy_menu")
async def buy_menu_callback(call: CallbackQuery):
    categories = await get_all_categories()
    keyboard_buttons = []
    
    details_text = "📂 **Chọn loại tài khoản bạn muốn mua:**\n\n"
    
    if not categories:
        details_text += "⚠️ *Shop chưa cập nhật sản phẩm*"
        keyboard_buttons.append([InlineKeyboardButton(text="⚠️ Shop chưa cập nhật sản phẩm", callback_data="back_start")])
    else:
        for idx, cat in enumerate(categories, 1):
            cat_code, cat_name, price, format_desc = cat['cat_code'], cat['cat_name'], cat['price'], cat['format_desc']
            count = await get_stock_count(cat_code)
            
            details_text += f"🔹 **{idx}.** `{cat_name}`\n   💰 Giá: `{price:,}đ` | 📦 Còn: `{count}`\n\n"
            
            short_name = cat_name[:35] + "..." if len(cat_name) > 35 else cat_name
            btn_text = f"{idx}. {short_name} ({price:,}đ)"
            keyboard_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"buy_{cat_code}")])
            
    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    details_text += "👇 *Nhấn vào nút tương ứng bên dưới để mua:*"
    await call.message.edit_text(details_text, reply_markup=keyboard, parse_mode="Markdown")
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("buy_"))
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
    new_balance = await get_user_balance(user_id)

    file_content = "\n".join(accounts)
    file_bytes = file_content.encode('utf-8')
    txt_file = BufferedInputFile(file_bytes, filename=f"Accounts_{quantity}pcs.txt")

    success_text = (
        f"✅ **Giao dịch thành công!**\n"
        f"📦 Loại: `{cat_name}`\n"
        f"🔢 Số lượng: `{quantity}` con\n"
        f"💵 Tổng tiền: `{total_price:,} VNĐ`\n"
        f"💰 Số dư ví còn lại: `{new_balance:,} VNĐ`\n\n"
        f"🛡️ *Bảo hành 1 đổi 1 lỗi lần đầu đăng nhập.*\n"
        f"📞 *Hỗ trợ / Khiếu nại liên hệ Telegram:* `{SUPPORT_TELEGRAM}`\n\n"
        f"📄 *Danh sách tài khoản của bạn đã được đính kèm ở file bên dưới:*"
    )
    
    await message_target.answer(success_text, parse_mode="Markdown")
    await message_target.answer_document(document=txt_file)
    await state.clear()

@dp.message(lambda message: message.document is not None)
async def handle_document_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()

    document = message.document
    file_name = document.file_name
    
    if not file_name.endswith('.txt'):
        await message.reply("⚠️ Vui lòng gửi file có định dạng `.txt`!", parse_mode="Markdown")
        return

    cat_code, cat_name, price, format_desc = get_category_info_by_filename(file_name)

    file_info = await bot.get_file(document.file_id)
    file_path = file_info.file_path
    
    downloaded_file = await bot.download_file(file_path)
    file_content = downloaded_file.read().decode('utf-8', errors='ignore')

    lines = file_content.split('\n')
    added_count = 0

    async with db_pool.acquire() as conn:
        exists = await conn.fetchval('SELECT cat_code FROM categories WHERE cat_code = $1', cat_code)
        if not exists:
            await conn.execute(
                'INSERT INTO categories (cat_code, cat_name, price, format_desc) VALUES ($1, $2, $3, $4)', 
                cat_code, cat_name, price, format_desc
            )

        for line in lines:
            line = line.strip()
            if line:
                await conn.execute('INSERT INTO stock (cat_code, account_info) VALUES ($1, $2)', cat_code, line)
                added_count += 1

    await message.reply(
        f"📥 **Đã nhập kho thành công!**\n"
        f"- Tên file: `{file_name}`\n"
        f"- Phân loại vào: **{cat_name[:30]}...**\n"
        f"- Đã thêm: **{added_count}** tài khoản vào kho.",
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
        
        raw_amount = data.get("transferAmount") or data.get("amount") or 0
        try:
            transfer_amount = int(float(raw_amount))
        except (ValueError, TypeError):
            transfer_amount = 0

        content = data.get("content") or data.get("description") or ""

        if transfer_type and str(transfer_type).lower() != "in":
            return web.json_response({"success": True})

        if not sepay_id:
            return web.json_response({"success": False, "error": "Missing transaction id"}, status=400)

        async with db_pool.acquire() as conn:
            exists = await conn.fetchval('SELECT sepay_id FROM transactions WHERE sepay_id = $1', int(sepay_id))
            if exists:
                return web.json_response({"success": True})

            # 🔥 NẾU DƯỚI 10K THÌ LƯU LẠI GIAO DỊCH NHƯNG GIỮ LUÔN TIỀN (KHÔNG CỘNG VÍ)
            if transfer_amount < 10000:
                await conn.execute(
                    'INSERT INTO transactions (sepay_id, user_id, amount) VALUES ($1, $2, $3)', 
                    int(sepay_id), 0, transfer_amount
                )
                logging.info(f"Khách chuyển dưới 10k ({transfer_amount}đ), bot đã nuốt tiền và không cộng ví.")
                return web.json_response({"success": True})

            match = re.search(r'NAP\D*(\d+)', str(content), re.IGNORECASE)
            if match:
                target_user_id = int(match.group(1))
                
                user_check = await conn.fetchrow('SELECT balance FROM users WHERE user_id = $1', target_user_id)
                if not user_check:
                    await conn.execute('INSERT INTO users (user_id, balance) VALUES ($1, 0)', target_user_id)
                
                await conn.execute('UPDATE users SET balance = balance + $1 WHERE user_id = $2', transfer_amount, target_user_id)
                await conn.execute('INSERT INTO transactions (sepay_id, user_id, amount) VALUES ($1, $2, $3)', int(sepay_id), target_user_id, transfer_amount)
                
                new_bal = await conn.fetchval('SELECT balance FROM users WHERE user_id = $1', target_user_id)
                
                try:
                    await bot.send_message(
                        target_user_id,
                        f"🎉 **NẠP TIỀN THÀNH CÔNG!**\n\n"
                        f"💵 Nhận: `+{transfer_amount:,} VNĐ`\n"
                        f"💰 Số dư ví: `{new_bal:,} VNĐ`",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logging.error(f"Lỗi gửi tin nhắn Telegram cho user {target_user_id}: {e}")
            else:
                logging.warning(f"Không tìm thấy cú pháp NAP trong nội dung: '{content}'")

        return web.json_response({"success": True})

    except Exception as e:
        logging.error(f"LỖI NGHIÊM TRỌNG TRONG WEBHOOK SEPAY: {str(e)}", exc_info=True)
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
                                "Shop vẫn hoạt động 24/7. AE cần mua clone hoặc key tool giá rẻ cứ ghé shop ủng hộ mình nhé!",
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
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
