@dp.callback_query(lambda c: c.data.startswith("buykey_"))
async def process_buy_key(call: CallbackQuery):
    days = int(call.data.replace("buykey_", ""))
    price = days * 1000  # 1k 1 ngày 1 key
    user_id = int(call.from_user.id) # 👈 Ép kiểu rõ ràng sang int ở đây

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
                   VALUES ($1, $2, TRUE, $3, $4::bigint)''', # 👈 Thêm ép kiểu ::bigint ở tham số thứ 4 phòng hờ
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
