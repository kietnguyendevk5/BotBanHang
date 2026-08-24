import logging
import sqlite3
import asyncio
import re
import os
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

WEBHOOK_HOST = '0.0.0.0'
# Cấu hình API Key xác thực từ SePay (Lấy từ biến môi trường hoặc mặc định)
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "spsk_test_zFCU1AguPj8T7RqzMAMRxSbgaspYi99y")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Định nghĩa trạng thái FSM để nhận số lượng khách nhập
class BuyState(StatesGroup):
    waiting_for_quantity = State()

# ==================== KHỞI TẠO DATABASE ĐỘNG ====================
def init_db():
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            cat_code TEXT PRIMARY KEY,
            cat_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            format_desc TEXT DEFAULT 'UID | Pass | 2FA'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cat_code TEXT NOT NULL,
            account_info TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            sepay_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            amount INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- CÁC HÀM XỬ LÝ DATABASE ---
def get_user_balance(user_id):
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('INSERT INTO users (user_id, balance) VALUES (?, 0)', (user_id,))
        conn.commit()
        balance = 0
    else:
        balance = row[0]
    conn.close()
    return balance

def update_balance(user_id, amount):
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def get_all_categories():
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT cat_code, cat_name, price, format_desc FROM categories')
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_stock_count(cat_code):
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM stock WHERE cat_code = ?', (cat_code,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def buy_multiple_accounts_from_stock(cat_code, quantity):
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, account_info FROM stock WHERE cat_code = ? LIMIT ?', (cat_code, quantity))
    rows = cursor.fetchall()
    
    if not rows:
        conn.close()
        return []
    
    acc_ids = [row[0] for row in rows]
    acc_infos = [row[1] for row in rows]
    
    placeholders = ','.join(['?'] * len(acc_ids))
    cursor.execute(f'DELETE FROM stock WHERE id IN ({placeholders})', acc_ids)
    conn.commit()
    conn.close()
    return acc_infos

# ==================== HÀM PHÂN LOẠI DỰA TRÊN TÊN FILE ====================
def get_category_info_by_filename(filename):
    fname = filename.upper()
    
    if "TUT" in fname and "TRAU" in fname:
        return (
            "cat_tut_trau",
            "CLONE CHƠI TUT TRÂU - AVT - NAME THÁI - HOTMAIL - AVT - CHẠY JOBS - LIVE ALL 100%",
            3500,
            "UID | Pass | Hotmail | Pass Hotmail | Cookie | Token"
        )
    elif "FIX" in fname or "VIET" in fname:
        return (
            "cat_fix_viet",
            "CLONE FIX UP CHUẨN NAME VIỆT - CHƠI TUT - VER HOTMAIL - AVT - CHẠY JOBS - LIVE ALL 100%",
            3500,
            "UID | Pass | Hotmail | Pass Hotmail | Cookie"
        )
    elif "BM" in fname:
        return (
            "cat_bm",
            "Clone New đã qua BM",
            3000,
            "Hàng login qua cookies, ae log id pass tets trước khi dùng"
        )
    else:
        return (
            "cat_new_zin",
            "CLONE CHƠI TUT - VER HOTMAIL - - LIVE ALL 100% - NEW ZIN",
            3000,
            "UID | PASS | HOTMAIL| COOKIE|TOKEN EAAAAU"
        )

# ==================== CÁC LỆNH CỦA BOT TELEGRAM ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    get_user_balance(user_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Mua Tài Khoản", callback_data="buy_menu"),
            InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit")
        ],
        [
            InlineKeyboardButton(text="👤 Tài Khoản Của Tôi", callback_data="profile")
        ]
    ])
    
    await message.answer(
        f"🤖 **HỆ THỐNG BÁN VIA/CLONE TỰ ĐỘNG**\n\n"
        f"Chào mừng bạn đến với shop! Vui lòng chọn chức năng bên dưới:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == "profile")
async def profile_callback(call: CallbackQuery):
    user_id = call.from_user.id
    balance = get_user_balance(user_id)
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
                        f"⚠️ *Dùng app ngân hàng quét mã QR để nạp tự động sau vài giây.*"
                    )
                    await call.message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")
                else:
                    await call.message.answer("❌ Không thể tạo mã QR lúc này!")
    except Exception as e:
        logging.error(f"Lỗi tải mã QR: {e}")
        await call.message.answer(f"💰 Chuyển khoản thủ công:\n- STK: `{BANK_ACCOUNT}`\n- Nội dung: `{syntax}`")

    await call.answer()

def urllib_quote(text):
    import urllib.parse
    return urllib.parse.quote(text)

@dp.callback_query(lambda c: c.data == "buy_menu")
async def buy_menu_callback(call: CallbackQuery):
    categories = get_all_categories()
    keyboard_buttons = []
    
    if not categories:
        keyboard_buttons.append([InlineKeyboardButton(text="⚠️ Shop chưa cập nhật sản phẩm", callback_data="back_start")])
    else:
        for cat_code, cat_name, price, format_desc in categories:
            count = get_stock_count(cat_code)
            short_name = cat_name[:40] + "..." if len(cat_name) > 40 else cat_name
            btn_text = f"{short_name} ({price:,}đ) - Còn: {count}"
            keyboard_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"buy_{cat_code}")])
            
    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_start")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    await call.message.edit_text("📂 **Chọn loại tài khoản bạn muốn mua:**", reply_markup=keyboard, parse_mode="Markdown")
    await call.answer()

@dp.callback_query(lambda c: c.data == "back_start")
async def back_start_callback(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Mua Tài Khoản", callback_data="buy_menu"),
            InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit")
        ],
        [
            InlineKeyboardButton(text="👤 Tài Khoản Của Tôi", callback_data="profile")
        ]
    ])
    await call.message.edit_text(
        f"🤖 **HỆ THỐNG BÁN VIA/CLONE TỰ ĐỘNG**\n\n"
        f"Chào mừng bạn trở lại! Vui lòng chọn chức năng bên dưới:",
        reply_markup=keyboard, 
        parse_mode="Markdown"
    )
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("buy_"))
async def process_buy_category(call: CallbackQuery, state: FSMContext):
    cat_code = call.data.replace("buy_", "")
    stock_count = get_stock_count(cat_code)

    if stock_count == 0:
        await call.answer("❌ Loại này tạm hết hàng, vui lòng chọn loại khác!", show_alert=True)
        return

    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT cat_name, price, format_desc FROM categories WHERE cat_code = ?', (cat_code,))
    cat_info = cursor.fetchone()
    conn.close()

    cat_name, price, format_desc = cat_info

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
    if not message.text.isdigit():
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
    balance = get_user_balance(user_id)

    if balance < total_price:
        await message_target.answer(f"❌ Số dư không đủ! Cần `{total_price:,} VNĐ` nhưng ví của bạn chỉ có `{balance:,} VNĐ`.", parse_mode="Markdown")
        await state.clear()
        return

    update_balance(user_id, -total_price)
    accounts = buy_multiple_accounts_from_stock(cat_code, quantity)
    new_balance = get_user_balance(user_id)

    file_content = "\n".join(accounts)
    file_bytes = file_content.encode('utf-8')
    txt_file = BufferedInputFile(file_bytes, filename=f"Accounts_{quantity}pcs.txt")

    success_text = (
        f"✅ **Giao dịch thành công!**\n"
        f"📦 Loại: `{cat_name}`\n"
        f"🔢 Số lượng: `{quantity}` con\n"
        f"💵 Tổng tiền: `{total_price:,} VNĐ`\n"
        f"💰 Số dư ví còn lại: `{new_balance:,} VNĐ`\n\n"
        f"📄 *Danh sách tài khoản của bạn đã được đính kèm ở file bên dưới:*"
    )
    
    await message_target.answer(success_text, parse_mode="Markdown")
    await message_target.answer_document(document=txt_file)
    await state.clear()

# ==================== TÍNH NĂNG NHẬN FILE .TXT VÀ ĐỌC TÊN FILE ====================
@dp.message(lambda message: message.document is not None)
async def handle_document_upload(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

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

    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT cat_code FROM categories WHERE cat_code = ?', (cat_code,))
    if not cursor.fetchone():
        cursor.execute(
            'INSERT INTO categories (cat_code, cat_name, price, format_desc) VALUES (?, ?, ?, ?)', 
            (cat_code, cat_name, price, format_desc)
        )

    for line in lines:
        line = line.strip()
        if line:
            cursor.execute('INSERT INTO stock (cat_code, account_info) VALUES (?, ?)', (cat_code, line))
            added_count += 1
            
    conn.commit()
    conn.close()

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
        get_user_balance(target_user_id)
        update_balance(target_user_id, amount)
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

        conn = sqlite3.connect('shop.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                sepay_id INTEGER PRIMARY KEY,
                user_id INTEGER,
                amount INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('SELECT sepay_id FROM transactions WHERE sepay_id = ?', (sepay_id,))
        if cursor.fetchone():
            conn.close()
            return web.json_response({"success": True})

        # Cho phép tìm từ khóa NAP theo sau là khoảng trắng và chuỗi số user_id (dù phía trước có mã giao dịch)
        match = re.search(r'NAP\D*(\d+)', str(content), re.IGNORECASE)
        if match:
            target_user_id = int(match.group(1))
            get_user_balance(target_user_id)
            update_balance(target_user_id, transfer_amount)

            cursor.execute('INSERT INTO transactions (sepay_id, user_id, amount) VALUES (?, ?, ?)', (sepay_id, target_user_id, transfer_amount))
            conn.commit()
            conn.close()

            try:
                new_bal = get_user_balance(target_user_id)
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
            conn.close()
            logging.warning(f"Không tìm thấy cú pháp NAP trong nội dung: '{content}'")

        return web.json_response({"success": True})

    except Exception as e:
        logging.error(f"LỖI NGHIÊM TRỌNG TRONG WEBHOOK SEPAY: {str(e)}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def main():
    app = web.Application()
    app.router.add_post('/api/webhook/sepay', sepay_webhook_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, WEBHOOK_HOST, port)
    await site.start()
    print(f"🌐 Webhook Server đang chạy tại cổng {port}...")

    print("🤖 Bot Telegram đang khởi động...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())