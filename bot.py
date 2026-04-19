import asyncio
import logging
import os
import asyncpg
import aiohttp
import ssl
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ContentType, BotCommand, BotCommandScopeChat
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_IDS = [7973988177]
SHOP_NAME = "Vest Creator"
SUPPORT_USERNAME = "@VestSupport"

SBP_PHONE = "+79818376180"
SBP_BANK = "ЮМАНИ"
RUB_PER_USDT = 90

# Premium Emoji IDs
EMOJI = {
    "settings": "5870982283724328568",
    "profile": "5870994129244131212",
    "users": "5870772616305839506",
    "shop": "5873147866364514353",
    "support": "6037249452824072506",
    "stats": "5870921681735781843",
    "media": "6035128606563241721",
    "broadcast": "6039422865189638057",
    "products": "5884479287171485878",
    "categories": "5870528606328852614",
    "back": "5893057118545646106",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "edit": "5870676941614354370",
    "delete": "5870875489362513438",
    "add": "5771851822897566479",
    "money": "5904462880941545555",
    "wallet": "5769126056262898415",
    "box": "5884479287171485878",
    "cryptobot": "5260752406890711732",
    "calendar": "5890937706803894250",
    "clock": "5983150113483134607",
    "download": "6039802767931871481",
    "send": "5963103826075456248",
    "info": "6028435952299413210",
    "bot": "6030400221232501136",
    "file": "5870528606328852614",
    "house": "5873147866364514353",
    "write": "5870753782874246579",
    "paper": "5778479949572738874",
    "phone": "6032644646587338669",
    "bank": "5873147866364514353",
    "loading": "5345906554510012647"
}

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)


# ==================== FSM States ====================
class AdminStates(StatesGroup):
    broadcast_text = State()
    set_media_file = State()
    add_category_name = State()
    add_product_name = State()
    add_product_desc = State()
    add_product_price = State()
    add_product_content = State()
    add_product_files = State()
    edit_shop_info = State()


# ВРЕМЕННОЕ ХРАНИЛИЩЕ ДЛЯ SBP
sbp_temp = {}


# ==================== Database ====================
_pool = None

async def get_db_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool

async def init_db():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                total_purchases INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                registered_at TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                content TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS product_files (
                id SERIAL PRIMARY KEY,
                product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
                file_id TEXT NOT NULL
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
                price REAL,
                purchased_at TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS media_settings (
                key TEXT PRIMARY KEY,
                media_type TEXT,
                file_id TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS shop_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS crypto_payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_id INTEGER,
                invoice_id TEXT UNIQUE,
                amount_rub REAL,
                amount_usdt REAL,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sbp_payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_id INTEGER,
                amount REAL,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        ''')

async def add_user(user: types.User):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (user_id, username, first_name, registered_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO NOTHING
        ''', user.id, user.username, user.first_name, datetime.now().isoformat())

async def get_user(user_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)

async def get_stats():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        users = await conn.fetchval('SELECT COUNT(*) FROM users')
        purchases = await conn.fetchval('SELECT COUNT(*) FROM purchases')
        revenue = await conn.fetchval('SELECT COALESCE(SUM(price), 0) FROM purchases')
        products = await conn.fetchval('SELECT COUNT(*) FROM products WHERE is_active = 1')
    return users, purchases, revenue, products

async def get_categories():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch('SELECT * FROM categories ORDER BY id')

async def add_category(name: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO categories (name) VALUES ($1)', name)

async def delete_category(cat_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM categories WHERE id = $1', cat_id)

async def get_products_by_category(category_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch('SELECT * FROM products WHERE category_id = $1 AND is_active = 1', category_id)

async def get_product(product_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)

async def get_product_files(product_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch('SELECT file_id FROM product_files WHERE product_id = $1', product_id)

async def add_product(category_id, name, description, price, content=None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval('''
            INSERT INTO products (category_id, name, description, price, content, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        ''', category_id, name, description, price, content, datetime.now().isoformat())

async def add_product_file(product_id: int, file_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO product_files (product_id, file_id) VALUES ($1, $2)', product_id, file_id)

async def delete_product(product_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE products SET is_active = 0 WHERE id = $1', product_id)

async def add_purchase(user_id: int, product_id: int, price: float):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO purchases (user_id, product_id, price, purchased_at)
            VALUES ($1, $2, $3, $4)
        ''', user_id, product_id, price, datetime.now().isoformat())
        await conn.execute('''
            UPDATE users SET total_purchases = total_purchases + 1, 
            total_spent = total_spent + $1 WHERE user_id = $2
        ''', price, user_id)

async def get_user_purchases(user_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch('''
            SELECT p.*, pr.name as product_name FROM purchases p 
            JOIN products pr ON p.product_id = pr.id 
            WHERE p.user_id = $1 ORDER BY p.purchased_at DESC LIMIT 10
        ''', user_id)

async def set_media(key: str, media_type: str, file_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO media_settings (key, media_type, file_id) 
            VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET media_type = $2, file_id = $3
        ''', key, media_type, file_id)

async def get_media(key: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM media_settings WHERE key = $1', key)

async def delete_media(key: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM media_settings WHERE key = $1', key)

async def get_shop_setting(key: str, default: str = ""):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval('SELECT value FROM shop_settings WHERE key = $1', key)
        return result if result else default

async def set_shop_setting(key: str, value: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO shop_settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2
        ''', key, value)

async def save_crypto_payment(user_id: int, product_id: int, invoice_id: str, amount_rub: float, amount_usdt: float):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO crypto_payments (user_id, product_id, invoice_id, amount_rub, amount_usdt, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
        ''', user_id, product_id, invoice_id, amount_rub, amount_usdt, datetime.now().isoformat())

async def get_crypto_payment(invoice_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM crypto_payments WHERE invoice_id = $1', invoice_id)

async def update_crypto_payment_status(invoice_id: str, status: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE crypto_payments SET status = $1 WHERE invoice_id = $2', status, invoice_id)

async def save_sbp_payment(user_id: int, product_id: int, amount: float, screenshot_file_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        payment_id = await conn.fetchval('''
            INSERT INTO sbp_payments (user_id, product_id, amount, screenshot_file_id, created_at)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
        ''', user_id, product_id, amount, screenshot_file_id, datetime.now().isoformat())
        return payment_id

async def get_pending_sbp_payments():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch('SELECT * FROM sbp_payments WHERE status = $1 ORDER BY id DESC', 'pending')

async def get_sbp_payment(payment_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM sbp_payments WHERE id = $1', payment_id)

async def update_sbp_payment_status(payment_id: int, status: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE sbp_payments SET status = $1 WHERE id = $2', status, payment_id)

async def get_all_users():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id FROM users')
        return [row['user_id'] for row in rows]


# ==================== CryptoBot API ====================
async def create_crypto_invoice(amount_usdt: float, description: str, payload: str):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    data = {
        "asset": "USDT",
        "amount": str(amount_usdt),
        "description": description,
        "payload": payload,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{(await bot.get_me()).username}"
    }
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.json()
            if result.get("ok"):
                return result["result"]
    return None

async def check_crypto_invoice(invoice_id: str):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(url, headers=headers, params=params) as resp:
            result = await resp.json()
            if result.get("ok") and result["result"]["items"]:
                return result["result"]["items"][0]
    return None


# ==================== Keyboards ====================
def main_keyboard():
    return {
        "keyboard": [
            [
                {"text": "Купить", "icon_custom_emoji_id": EMOJI['shop']},
                {"text": "Профиль", "icon_custom_emoji_id": EMOJI['profile']}
            ],
            [
                {"text": "О шопе", "icon_custom_emoji_id": EMOJI['house']},
                {"text": "Поддержка", "icon_custom_emoji_id": EMOJI['support']}
            ]
        ],
        "resize_keyboard": True
    }

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Статистика", callback_data="admin_stats", icon_custom_emoji_id=EMOJI['stats'])],
        [InlineKeyboardButton(text="Медиа", callback_data="admin_media", icon_custom_emoji_id=EMOJI['media'])],
        [InlineKeyboardButton(text="Рассылка", callback_data="admin_broadcast", icon_custom_emoji_id=EMOJI['broadcast'])],
        [InlineKeyboardButton(text="Товары", callback_data="admin_products", icon_custom_emoji_id=EMOJI['products'])],
        [InlineKeyboardButton(text="Категории", callback_data="admin_categories", icon_custom_emoji_id=EMOJI['categories'])],
        [InlineKeyboardButton(text="СБП платежи", callback_data="admin_sbp_payments", icon_custom_emoji_id=EMOJI['money'])],
        [InlineKeyboardButton(text="Настройки", callback_data="admin_settings", icon_custom_emoji_id=EMOJI['settings'])]
    ])

def admin_back():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="admin_panel", icon_custom_emoji_id=EMOJI['back'])]
    ])

def back_button(callback_data: str = "main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data=callback_data, icon_custom_emoji_id=EMOJI['back'])]
    ])


# ==================== Helper Functions ====================
async def send_with_media(chat_id: int, text: str, media_key: str, reply_markup=None):
    media = await get_media(media_key)
    if media:
        if media['media_type'] == 'photo':
            await bot.send_photo(chat_id, media['file_id'], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media['media_type'] == 'video':
            await bot.send_video(chat_id, media['file_id'], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media['media_type'] == 'animation':
            await bot.send_animation(chat_id, media['file_id'], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)

async def set_commands(user_id: int):
    commands = [BotCommand(command="start", description="Старт")]
    if user_id in ADMIN_IDS:
        commands.append(BotCommand(command="admin", description="Админ панель"))
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))

async def deliver_product(user_id: int, product_id: int):
    product = await get_product(product_id)
    if not product:
        return

    text = f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Оплата успешна!</b>\n\n"
    text += f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Товар:</b> {product['name']}\n\n"

    if product['content']:
        text += f"<blockquote>{product['content']}</blockquote>\n\n"
        await bot.send_message(user_id, text, parse_mode="HTML")

    files = await get_product_files(product_id)
    if files:
        if not product['content']:
            await bot.send_message(user_id, text, parse_mode="HTML")
        for file_row in files:
            try:
                await bot.send_document(user_id, file_row['file_id'])
            except:
                pass
        await bot.send_message(
            user_id,
            f"<b><tg-emoji emoji-id='{EMOJI['download']}'>⬇</tg-emoji> Все файлы получены!</b>",
            parse_mode="HTML",
            reply_markup=back_button("shop")
        )
    elif not product['content']:
        await bot.send_message(user_id, "Товар не содержит данных.", reply_markup=back_button("shop"))
    else:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=back_button("shop"))


# ==================== Handlers ====================
@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user)
    await set_commands(message.from_user.id)

    text = f"<b><tg-emoji emoji-id='{EMOJI['house']}'>🏪</tg-emoji> {SHOP_NAME}</b>\n\n"
    text += f"<blockquote><tg-emoji emoji-id='{EMOJI['download']}'>👇</tg-emoji> Выберите действие:</blockquote>"

    await message.answer(text, parse_mode="HTML", reply_markup=main_keyboard())


@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()

    text = f"<blockquote><b><tg-emoji emoji-id='{EMOJI['bot']}'>🎩</tg-emoji> Админ панель</b></blockquote>"
    await message.answer(text, parse_mode="HTML", reply_markup=admin_keyboard())


@router.callback_query(F.data == "main")
async def cb_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = f"<b><tg-emoji emoji-id='{EMOJI['house']}'>🏪</tg-emoji> {SHOP_NAME}</b>\n\n"
    text += f"<blockquote><tg-emoji emoji-id='{EMOJI['download']}'>👇</tg-emoji> Выберите действие:</blockquote>"

    try:
        await callback.message.delete()
    except:
        pass
    await bot.send_message(callback.from_user.id, text, parse_mode="HTML", reply_markup=main_keyboard())
    await callback.answer()


# ==================== Text Button Handlers ====================
@router.message(F.text == "Купить")
async def text_shop(message: types.Message):
    categories = await get_categories()
    if not categories:
        await message.answer("Категории пока не добавлены")
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(
            text=cat['name'],
            callback_data=f"cat_{cat['id']}",
            icon_custom_emoji_id=EMOJI['categories']
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="main",
        icon_custom_emoji_id=EMOJI['back']
    )])

    text = f"<b><tg-emoji emoji-id='{EMOJI['shop']}'>🛒</tg-emoji> Каталог товаров</b>\n\n"
    text += f"<blockquote><tg-emoji emoji-id='{EMOJI['download']}'>👇</tg-emoji> Выберите категорию:</blockquote>"

    await send_with_media(message.chat.id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.message(F.text == "Профиль")
async def text_profile(message: types.Message):
    user = await get_user(message.from_user.id)
    purchases = await get_user_purchases(message.from_user.id)

    text = f"<b><tg-emoji emoji-id='{EMOJI['profile']}'>👤</tg-emoji> Мой профиль</b>\n\n"
    text += f"<tg-emoji emoji-id='{EMOJI['info']}'>🆔</tg-emoji> <b>ID:</b> <code>{message.from_user.id}</code>\n"
    text += f"<tg-emoji emoji-id='{EMOJI['box']}'>🛒</tg-emoji> <b>Покупок:</b> {user['total_purchases']}\n"
    text += f"<tg-emoji emoji-id='{EMOJI['money']}'>💵</tg-emoji> <b>Потрачено:</b> {user['total_spent']:.0f}₽\n"
    text += f"<tg-emoji emoji-id='{EMOJI['calendar']}'>📅</tg-emoji> <b>Регистрация:</b> {user['registered_at'][:10]}\n"

    if purchases:
        text += "\n<b><tg-emoji emoji-id='{EMOJI['paper']}'>📋</tg-emoji> Последние покупки:</b>\n"
        for p in purchases[:5]:
            text += f"• {p['product_name']} — {p['price']:.0f}₽\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мои покупки", callback_data="my_purchases", icon_custom_emoji_id=EMOJI['box'])]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(F.text == "О шопе")
async def text_about(message: types.Message):
    info = await get_shop_setting("shop_info", "Информация о магазине не заполнена.")
    text = f"<b><tg-emoji emoji-id='{EMOJI['house']}'>🏬</tg-emoji> О шопе</b>\n\n<blockquote>{info}</blockquote>"
    await send_with_media(message.chat.id, text, "about_menu", None)


@router.message(F.text == "Поддержка")
async def text_support(message: types.Message):
    text = f"<b><tg-emoji emoji-id='{EMOJI['support']}'>🛟</tg-emoji> Поддержка</b>\n\n"
    text += f"<blockquote>По всем вопросам обращайтесь: {SUPPORT_USERNAME}</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Написать",
            url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}",
            icon_custom_emoji_id=EMOJI['send']
        )]
    ])

    await send_with_media(message.chat.id, text, "support_menu", keyboard)


# ==================== Shop ====================
@router.callback_query(F.data == "shop")
async def cb_shop(callback: types.CallbackQuery):
    categories = await get_categories()
    if not categories:
        await callback.answer("Категории пока не добавлены", show_alert=True)
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(
            text=cat['name'],
            callback_data=f"cat_{cat['id']}",
            icon_custom_emoji_id=EMOJI['categories']
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="main",
        icon_custom_emoji_id=EMOJI['back']
    )])

    text = f"<b><tg-emoji emoji-id='{EMOJI['shop']}'>🛒</tg-emoji> Каталог товаров</b>\n\n"
    text += f"<blockquote><tg-emoji emoji-id='{EMOJI['download']}'>👇</tg-emoji> Выберите категорию:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@router.callback_query(F.data.startswith("cat_"))
async def cb_category(callback: types.CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    products = await get_products_by_category(cat_id)

    if not products:
        await callback.answer("В этой категории пока нет товаров", show_alert=True)
        return

    keyboard = []
    for prod in products:
        keyboard.append([InlineKeyboardButton(
            text=f"{prod['name']} — {prod['price']:.0f}₽",
            callback_data=f"prod_{prod['id']}",
            icon_custom_emoji_id=EMOJI['box']
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="shop",
        icon_custom_emoji_id=EMOJI['back']
    )])

    await callback.message.edit_text(
        f"<blockquote><tg-emoji emoji-id='{EMOJI['download']}'>👇</tg-emoji> Выберите товар:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prod_"))
async def cb_product(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> {product['name']}</b>\n\n"
    text += f"<blockquote>{product['description']}</blockquote>\n\n"
    text += f"<b><tg-emoji emoji-id='{EMOJI['money']}'>💵</tg-emoji> Цена:</b> {product['price']:.0f}₽"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Купить",
            callback_data=f"buy_{prod_id}",
            icon_custom_emoji_id=EMOJI['wallet']
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data=f"cat_{product['category_id']}",
            icon_custom_emoji_id=EMOJI['back']
        )]
    ])

    try:
        await callback.message.delete()
    except:
        pass
    await send_with_media(callback.from_user.id, text, f"product_{prod_id}", keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("buy_"))
async def cb_buy(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = f"<b><tg-emoji emoji-id='{EMOJI['wallet']}'>💳</tg-emoji> Выберите способ оплаты</b>\n\n"
    text += f"<tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> <b>Товар:</b> {product['name']}\n"
    text += f"<tg-emoji emoji-id='{EMOJI['money']}'>💵</tg-emoji> <b>Сумма:</b> {product['price']:.0f}₽\n\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="CryptoBot (USDT)",
            callback_data=f"crypto_{prod_id}",
            icon_custom_emoji_id=EMOJI['cryptobot']
        )],
        [InlineKeyboardButton(
            text="СБП (Карта)",
            callback_data=f"sbp_{prod_id}",
            icon_custom_emoji_id=EMOJI['money']
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data=f"prod_{prod_id}",
            icon_custom_emoji_id=EMOJI['back']
        )]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ==================== CryptoBot Payment ====================
@router.callback_query(F.data.startswith("crypto_"))
async def cb_crypto_payment(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    amount_rub = product['price']
    amount_usdt = round(amount_rub / RUB_PER_USDT, 2)

    invoice = await create_crypto_invoice(
        amount_usdt=amount_usdt,
        description=f"Покупка: {product['name']}",
        payload=f"{callback.from_user.id}:{prod_id}"
    )

    if not invoice:
        await callback.answer("Ошибка создания счёта. Попробуйте позже.", show_alert=True)
        return

    await save_crypto_payment(callback.from_user.id, prod_id, str(invoice['invoice_id']), amount_rub, amount_usdt)

    text = f"<b><tg-emoji emoji-id='{EMOJI['cryptobot']}'>💎</tg-emoji> Оплата через CryptoBot</b>\n\n"
    text += f"<tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> <b>Товар:</b> {product['name']}\n"
    text += f"<tg-emoji emoji-id='{EMOJI['money']}'>💵</tg-emoji> <b>Сумма:</b> {amount_usdt} USDT\n\n"
    text += "<blockquote>Нажмите «Оплатить» и после оплаты нажмите «Проверить оплату»</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=invoice['pay_url'], icon_custom_emoji_id=EMOJI['wallet'])],
        [InlineKeyboardButton(
            text="Проверить оплату",
            callback_data=f"checkcrypto_{invoice['invoice_id']}",
            icon_custom_emoji_id=EMOJI['check']
        )],
        [InlineKeyboardButton(text="Отмена", callback_data=f"prod_{prod_id}", icon_custom_emoji_id=EMOJI['cross'])]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("checkcrypto_"))
async def cb_check_crypto(callback: types.CallbackQuery):
    invoice_id = callback.data.split("_")[1]
    invoice = await check_crypto_invoice(invoice_id)

    if not invoice:
        await callback.answer("Ошибка проверки платежа", show_alert=True)
        return

    payment = await get_crypto_payment(invoice_id)

    if invoice['status'] == 'paid' and payment and payment['status'] == 'pending':
        await update_crypto_payment_status(invoice_id, 'paid')
        await add_purchase(payment['user_id'], payment['product_id'], payment['amount_rub'])
        await deliver_product(payment['user_id'], payment['product_id'])

        product = await get_product(payment['product_id'])
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"<b>💰 Новая покупка (CryptoBot)!</b>\n\n"
                    f"👤 Пользователь: {callback.from_user.id}\n"
                    f"📦 Товар: {product['name']}\n"
                    f"💵 Сумма: {payment['amount_rub']:.0f}₽ ({payment['amount_usdt']} USDT)",
                    parse_mode="HTML"
                )
            except:
                pass
        await callback.answer("✅ Оплата успешна! Товар выдан.", show_alert=True)
    elif invoice['status'] == 'paid':
        await callback.answer("Товар уже выдан!", show_alert=True)
    else:
        await callback.answer("Оплата не найдена. Попробуйте позже.", show_alert=True)


# ==================== SBP Payment ====================
@router.callback_query(F.data.startswith("sbp_"))
async def cb_sbp_payment(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    sbp_temp[callback.from_user.id] = {
        'prod_id': prod_id,
        'amount': product['price']
    }

    text = f"<b><tg-emoji emoji-id='{EMOJI['money']}'>💳</tg-emoji> Оплата через СБП</b>\n\n"
    text += f"<tg-emoji emoji-id='{EMOJI['phone']}'>📱</tg-emoji> <b>Номер:</b> <code>{SBP_PHONE}</code>\n"
    text += f"<tg-emoji emoji-id='{EMOJI['bank']}'>🏦</tg-emoji> <b>Банк:</b> {SBP_BANK}\n"
    text += f"<tg-emoji emoji-id='{EMOJI['money']}'>💵</tg-emoji> <b>Сумма:</b> {product['price']:.0f}₽\n\n"
    text += "<blockquote>1. Переведите сумму по номеру через СБП\n"
    text += "2. Нажмите кнопку «Я оплатил» и отправьте скриншот (фото или файл)</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Я оплатил",
            callback_data="i_paid_sbp",
            icon_custom_emoji_id=EMOJI['check']
        )],
        [InlineKeyboardButton(
            text="Отмена",
            callback_data=f"prod_{prod_id}",
            icon_custom_emoji_id=EMOJI['cross']
        )]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "i_paid_sbp")
async def cb_i_paid_sbp(callback: types.CallbackQuery):
    if callback.from_user.id not in sbp_temp:
        await callback.answer("Сессия истекла, начните заново", show_alert=True)
        return

    text = f"<b><tg-emoji emoji-id='{EMOJI['file']}'>📎</tg-emoji> Отправьте скриншот оплаты</b>\n\n"
    text += "<blockquote>Отправьте фото или файл скриншота перевода.</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Отмена",
            callback_data="shop",
            icon_custom_emoji_id=EMOJI['cross']
        )]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.message(F.photo)
async def handle_sbp_photo(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in sbp_temp:
        return
    
    data = sbp_temp[user_id]
    prod_id = data['prod_id']
    amount = data['amount']
    
    del sbp_temp[user_id]
    
    product = await get_product(prod_id)
    file_id = message.photo[-1].file_id
    
    payment_id = await save_sbp_payment(user_id, prod_id, amount, file_id)
    
    for admin_id in ADMIN_IDS:
        try:
            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_sbp_{payment_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_sbp_{payment_id}")
                ]
            ])
            
            caption = (
                f"💰 <b>НОВАЯ ОПЛАТА СБП!</b>\n\n"
                f"👤 @{message.from_user.username or 'Без username'}\n"
                f"🆔 <code>{user_id}</code>\n"
                f"📦 {product['name']}\n"
                f"💵 {amount:.0f}₽\n"
                f"🔑 ID: <code>{payment_id}</code>"
            )
            
            await bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=admin_kb
            )
            print(f"[SBP] Фото отправлено админу {admin_id}")
        except Exception as e:
            print(f"[SBP] Ошибка отправки админу {admin_id}: {e}")
    
    await message.answer(
        f"<b><tg-emoji emoji-id='{EMOJI['clock']}'>⏰</tg-emoji> Скриншот отправлен на проверку!</b>\n\n"
        f"<blockquote>Ожидайте подтверждения.\nID: <code>{payment_id}</code></blockquote>",
        parse_mode="HTML",
        reply_markup=back_button("shop")
    )


@router.message(F.document)
async def handle_sbp_document(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in sbp_temp:
        return
    
    data = sbp_temp[user_id]
    prod_id = data['prod_id']
    amount = data['amount']
    
    del sbp_temp[user_id]
    
    product = await get_product(prod_id)
    file_id = message.document.file_id
    
    payment_id = await save_sbp_payment(user_id, prod_id, amount, file_id)
    
    for admin_id in ADMIN_IDS:
        try:
            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_sbp_{payment_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_sbp_{payment_id}")
                ]
            ])
            
            caption = (
                f"💰 <b>НОВАЯ ОПЛАТА СБП!</b>\n\n"
                f"👤 @{message.from_user.username or 'Без username'}\n"
                f"🆔 <code>{user_id}</code>\n"
                f"📦 {product['name']}\n"
                f"💵 {amount:.0f}₽\n"
                f"🔑 ID: <code>{payment_id}</code>"
            )
            
            await bot.send_document(
                chat_id=admin_id,
                document=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=admin_kb
            )
            print(f"[SBP] Файл отправлен админу {admin_id}")
        except Exception as e:
            print(f"[SBP] Ошибка отправки админу {admin_id}: {e}")
    
    await message.answer(
        f"<b><tg-emoji emoji-id='{EMOJI['clock']}'>⏰</tg-emoji> Скриншот отправлен на проверку!</b>\n\n"
        f"<blockquote>Ожидайте подтверждения.\nID: <code>{payment_id}</code></blockquote>",
        parse_mode="HTML",
        reply_markup=back_button("shop")
    )


# ==================== Admin SBP Verification ====================
@router.callback_query(F.data == "admin_sbp_payments")
async def cb_admin_sbp_payments(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return

    payments = await get_pending_sbp_payments()

    if not payments:
        await callback.answer("Нет ожидающих платежей", show_alert=True)
        return

    text = f"<b><tg-emoji emoji-id='{EMOJI['money']}'>💰</tg-emoji> Ожидающие СБП платежи</b>\n\n"
    for p in payments[:10]:
        product = await get_product(p['product_id'])
        text += f"🆔 #{p['id']} | {p['amount']:.0f}₽ | {product['name']}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="admin_panel", icon_custom_emoji_id=EMOJI['back'])]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("approve_sbp_"))
async def cb_approve_sbp(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return

    payment_id = int(callback.data.split("_")[2])
    payment = await get_sbp_payment(payment_id)

    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment['status'] != 'pending':
        await callback.answer("Платёж уже обработан", show_alert=True)
        return

    await update_sbp_payment_status(payment_id, 'approved')
    await add_purchase(payment['user_id'], payment['product_id'], payment['amount'])
    await deliver_product(payment['user_id'], payment['product_id'])

    try:
        await bot.send_message(
            payment['user_id'],
            f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Ваша оплата одобрена!</b>\n\nТовар выдан выше.",
            parse_mode="HTML"
        )
    except:
        pass

    try:
        if callback.message.caption:
            await callback.message.edit_caption(
                caption=f"{callback.message.caption}\n\n<b>✅ ОДОБРЕНО</b>",
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                text=f"{callback.message.text}\n\n<b>✅ ОДОБРЕНО</b>",
                parse_mode="HTML"
            )
    except:
        pass

    await callback.answer("✅ Оплата одобрена!")


@router.callback_query(F.data.startswith("reject_sbp_"))
async def cb_reject_sbp(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return

    payment_id = int(callback.data.split("_")[2])
    payment = await get_sbp_payment(payment_id)

    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment['status'] != 'pending':
        await callback.answer("Платёж уже обработан", show_alert=True)
        return

    await update_sbp_payment_status(payment_id, 'rejected')

    try:
        await bot.send_message(
            payment['user_id'],
            f"<b><tg-emoji emoji-id='{EMOJI['cross']}'>❌</tg-emoji> Ваша оплата отклонена.</b>\n\nСвяжитесь с поддержкой: {SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
    except:
        pass

    try:
        if callback.message.caption:
            await callback.message.edit_caption(
                caption=f"{callback.message.caption}\n\n<b>❌ ОТКЛОНЕНО</b>",
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                text=f"{callback.message.text}\n\n<b>❌ ОТКЛОНЕНО</b>",
                parse_mode="HTML"
            )
    except:
        pass

    await callback.answer("❌ Оплата отклонена!")


# ==================== Profile ====================
@router.callback_query(F.data == "my_purchases")
async def cb_my_purchases(callback: types.CallbackQuery):
    purchases = await get_user_purchases(callback.from_user.id)

    if not purchases:
        await callback.answer("У вас пока нет покупок", show_alert=True)
        return

    text = f"<b><tg-emoji emoji-id='{EMOJI['paper']}'>📜</tg-emoji> Мои покупки</b>\n\n"
    for p in purchases:
        text += f"<tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> {p['product_name']} — {p['price']:.0f}₽ ({p['purchased_at'][:10]})\n"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button())
    await callback.answer()


# ==================== Admin Handlers ====================
@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()

    text = f"<blockquote><b><tg-emoji emoji-id='{EMOJI['bot']}'>🎩</tg-emoji> Админ панель</b></blockquote>"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    users, purchases, revenue, products = await get_stats()

    text = f"<b><tg-emoji emoji-id='{EMOJI['stats']}'>📊</tg-emoji> Статистика</b>\n\n"
    text += f"<tg-emoji emoji-id='{EMOJI['users']}'>👥</tg-emoji> <b>Пользователей:</b> {users}\n"
    text += f"<tg-emoji emoji-id='{EMOJI['shop']}'>🛒</tg-emoji> <b>Покупок:</b> {purchases}\n"
    text += f"<tg-emoji emoji-id='{EMOJI['money']}'>💵</tg-emoji> <b>Выручка:</b> {revenue:.0f}₽\n"
    text += f"<tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> <b>Товаров:</b> {products}"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()


# ==================== Admin Media ====================
@router.callback_query(F.data == "admin_media")
async def cb_admin_media(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Главное меню", callback_data="setmedia_main_menu", icon_custom_emoji_id=EMOJI['house'])],
        [InlineKeyboardButton(text="Меню магазина", callback_data="setmedia_shop_menu", icon_custom_emoji_id=EMOJI['shop'])],
        [InlineKeyboardButton(text="О шопе", callback_data="setmedia_about_menu", icon_custom_emoji_id=EMOJI['info'])],
        [InlineKeyboardButton(text="Поддержка", callback_data="setmedia_support_menu", icon_custom_emoji_id=EMOJI['support'])],
        [InlineKeyboardButton(text="Назад", callback_data="admin_panel", icon_custom_emoji_id=EMOJI['back'])]
    ])

    text = f"<b><tg-emoji emoji-id='{EMOJI['media']}'>🖼</tg-emoji> Настройка медиа</b>\n\n"
    text += f"<blockquote>Выберите раздел для установки медиа:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("setmedia_"))
async def cb_setmedia(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    media_key = callback.data.replace("setmedia_", "")
    await state.update_data(media_key=media_key)
    await state.set_state(AdminStates.set_media_file)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Удалить медиа", callback_data=f"delmedia_{media_key}", icon_custom_emoji_id=EMOJI['delete'])],
        [InlineKeyboardButton(text="Назад", callback_data="admin_media", icon_custom_emoji_id=EMOJI['back'])]
    ])

    text = f"<b><tg-emoji emoji-id='{EMOJI['media']}'>🖼</tg-emoji> Установка медиа</b>\n\n"
    text += f"<blockquote>Отправьте фото, видео или GIF для этого раздела:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("delmedia_"))
async def cb_delmedia(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    media_key = callback.data.replace("delmedia_", "")
    await delete_media(media_key)

    await state.clear()
    await callback.answer("Медиа удалено", show_alert=True)
    await cb_admin_media(callback)


@router.message(StateFilter(AdminStates.set_media_file), F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION]))
async def process_media_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    media_key = data.get("media_key")

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    elif message.animation:
        file_id = message.animation.file_id
        media_type = "animation"
    else:
        await message.answer("Неподдерживаемый формат", reply_markup=admin_back())
        return

    await set_media(media_key, media_type, file_id)
    await state.clear()
    await message.answer(f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Медиа успешно установлено!</b>", parse_mode="HTML", reply_markup=admin_back())


# ==================== Admin Broadcast ====================
@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.broadcast_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="admin_panel", icon_custom_emoji_id=EMOJI['back'])]
    ])

    text = f"<b><tg-emoji emoji-id='{EMOJI['broadcast']}'>📨</tg-emoji> Рассылка</b>\n\n"
    text += f"<blockquote>Отправьте текст, фото, видео или GIF для рассылки:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.message(StateFilter(AdminStates.broadcast_text))
async def process_broadcast(message: types.Message, state: FSMContext):
    await state.clear()
    users = await get_all_users()

    success = 0
    failed = 0

    status_msg = await message.answer(f"<b><tg-emoji emoji-id='{EMOJI['loading']}'>📤</tg-emoji> Рассылка начата...</b>", parse_mode="HTML")

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.animation:
                await bot.send_animation(user_id, message.animation.file_id, caption=message.caption or "", parse_mode="HTML")
            else:
                await bot.send_message(user_id, message.text or "", parse_mode="HTML")
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Рассылка завершена!</b>\n\n"
        f"<tg-emoji emoji-id='{EMOJI['send']}'>📤</tg-emoji> Успешно: {success}\n"
        f"<tg-emoji emoji-id='{EMOJI['cross']}'>❌</tg-emoji> Ошибок: {failed}",
        parse_mode="HTML",
        reply_markup=admin_back()
    )


# ==================== Admin Categories ====================
@router.callback_query(F.data == "admin_categories")
async def cb_admin_categories(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()

    keyboard = []
    for cat in categories:
        keyboard.append([
            InlineKeyboardButton(
                text=cat['name'],
                callback_data=f"viewcat_{cat['id']}",
                icon_custom_emoji_id=EMOJI['categories']
            ),
            InlineKeyboardButton(
                text="",
                callback_data=f"delcat_{cat['id']}",
                icon_custom_emoji_id=EMOJI['delete']
            )
        ])
    keyboard.append([InlineKeyboardButton(
        text="Добавить категорию",
        callback_data="addcat",
        icon_custom_emoji_id=EMOJI['add']
    )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_panel",
        icon_custom_emoji_id=EMOJI['back']
    )])

    text = f"<b><tg-emoji emoji-id='{EMOJI['categories']}'>📁</tg-emoji> Категории</b>\n\n"
    text += f"<blockquote>Управление категориями товаров:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@router.callback_query(F.data == "addcat")
async def cb_addcat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.add_category_name)

    text = f"<b><tg-emoji emoji-id='{EMOJI['categories']}'>📁</tg-emoji> Новая категория</b>\n\n"
    text += f"<blockquote>Введите название категории:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()


@router.message(StateFilter(AdminStates.add_category_name))
async def process_category_name(message: types.Message, state: FSMContext):
    await add_category(message.text)
    await state.clear()
    await message.answer(f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Категория добавлена!</b>", parse_mode="HTML", reply_markup=admin_back())


@router.callback_query(F.data.startswith("delcat_"))
async def cb_delcat(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    await delete_category(cat_id)
    await callback.answer("Категория удалена", show_alert=True)
    await cb_admin_categories(callback)


# ==================== Admin Products ====================
@router.callback_query(F.data == "admin_products")
async def cb_admin_products(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(
            text=cat['name'],
            callback_data=f"admincat_{cat['id']}",
            icon_custom_emoji_id=EMOJI['categories']
        )])
    keyboard.append([InlineKeyboardButton(
        text="Добавить товар",
        callback_data="addprod",
        icon_custom_emoji_id=EMOJI['add']
    )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_panel",
        icon_custom_emoji_id=EMOJI['back']
    )])

    text = f"<b><tg-emoji emoji-id='{EMOJI['products']}'>📦</tg-emoji> Товары</b>\n\n"
    text += f"<blockquote>Выберите категорию для просмотра товаров:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@router.callback_query(F.data.startswith("admincat_"))
async def cb_admincat(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    products = await get_products_by_category(cat_id)

    keyboard = []
    for prod in products:
        keyboard.append([
            InlineKeyboardButton(
                text=f"{prod['name']} — {prod['price']:.0f}₽",
                callback_data=f"viewprod_{prod['id']}",
                icon_custom_emoji_id=EMOJI['box']
            ),
            InlineKeyboardButton(
                text="",
                callback_data=f"delprod_{prod['id']}",
                icon_custom_emoji_id=EMOJI['delete']
            )
        ])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_products",
        icon_custom_emoji_id=EMOJI['back']
    )])

    await callback.message.edit_text(
        f"<blockquote><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Товары в категории:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delprod_"))
async def cb_delprod(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    prod_id = int(callback.data.split("_")[1])
    await delete_product(prod_id)
    await callback.answer("Товар удален", show_alert=True)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_products",
            icon_custom_emoji_id=EMOJI['back']
        )]
    ])
    await callback.message.edit_text(f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Товар удален</b>", parse_mode="HTML", reply_markup=keyboard)


# ==================== Add Product Flow ====================
@router.callback_query(F.data == "addprod")
async def cb_addprod(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()
    if not categories:
        await callback.answer("Сначала создайте категорию!", show_alert=True)
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(
            text=cat['name'],
            callback_data=f"newprodcat_{cat['id']}",
            icon_custom_emoji_id=EMOJI['categories']
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_products",
        icon_custom_emoji_id=EMOJI['back']
    )])

    text = f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Новый товар</b>\n\n"
    text += f"<blockquote>Выберите категорию для товара:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@router.callback_query(F.data.startswith("newprodcat_"))
async def cb_newprodcat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    await state.update_data(category_id=cat_id)
    await state.set_state(AdminStates.add_product_name)

    text = f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Новый товар</b>\n\n"
    text += f"<blockquote>Введите название товара:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()


@router.message(StateFilter(AdminStates.add_product_name))
async def process_product_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.add_product_desc)

    text = f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Новый товар</b>\n\n"
    text += f"<blockquote>Введите описание товара:</blockquote>"

    await message.answer(text, parse_mode="HTML", reply_markup=admin_back())


@router.message(StateFilter(AdminStates.add_product_desc))
async def process_product_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AdminStates.add_product_price)

    text = f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Новый товар</b>\n\n"
    text += f"<blockquote>Введите цену в рублях:</blockquote>"

    await message.answer(text, parse_mode="HTML", reply_markup=admin_back())


@router.message(StateFilter(AdminStates.add_product_price))
async def process_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        await state.update_data(price=price)
        await state.set_state(AdminStates.add_product_content)
        await state.update_data(file_ids=[])

        text = f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Новый товар</b>\n\n"
        text += "<blockquote>Введите текстовое описание/контент товара (или отправьте \"-\" если не нужно):</blockquote>"

        await message.answer(text, parse_mode="HTML", reply_markup=admin_back())
    except ValueError:
        await message.answer("Введите корректную цену (число)")


@router.message(StateFilter(AdminStates.add_product_content))
async def process_product_content(message: types.Message, state: FSMContext):
    content = message.text if message.text != "-" else None
    await state.update_data(content=content)
    await state.set_state(AdminStates.add_product_files)

    text = f"<b><tg-emoji emoji-id='{EMOJI['box']}'>📦</tg-emoji> Новый товар</b>\n\n"
    text += "<blockquote>Отправьте файлы товара (до 10 файлов).\nКогда закончите, нажмите «Готово» (можно без файлов):</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Готово", callback_data="finish_product", icon_custom_emoji_id=EMOJI['check'])],
        [InlineKeyboardButton(text="Назад", callback_data="addprod", icon_custom_emoji_id=EMOJI['back'])]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(StateFilter(AdminStates.add_product_files), F.document)
async def process_product_files(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get('file_ids', [])

    if len(file_ids) >= 10:
        await message.answer("Максимум 10 файлов!")
        return

    file_ids.append(message.document.file_id)
    await state.update_data(file_ids=file_ids)

    await message.answer(f"<tg-emoji emoji-id='{EMOJI['file']}'>📎</tg-emoji> Файл добавлен! Всего: {len(file_ids)}/10")


@router.callback_query(F.data == "finish_product", StateFilter(AdminStates.add_product_files))
async def cb_finish_product(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get('file_ids', [])
    content = data.get('content')

    product_id = await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        content=content
    )

    for file_id in file_ids:
        await add_product_file(product_id, file_id)

    await state.clear()
    await callback.message.edit_text(f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Товар успешно добавлен!</b>", parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()


# ==================== Admin Settings ====================
@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Изменить описание магазина",
            callback_data="edit_shop_info",
            icon_custom_emoji_id=EMOJI['edit']
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_panel",
            icon_custom_emoji_id=EMOJI['back']
        )]
    ])

    text = f"<b><tg-emoji emoji-id='{EMOJI['settings']}'>⚙️</tg-emoji> Настройки</b>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "edit_shop_info")
async def cb_edit_shop_info(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.edit_shop_info)

    text = f"<b><tg-emoji emoji-id='{EMOJI['edit']}'>📝</tg-emoji> Описание магазина</b>\n\n"
    text += f"<blockquote>Введите новое описание магазина:</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()


@router.message(StateFilter(AdminStates.edit_shop_info))
async def process_shop_info(message: types.Message, state: FSMContext):
    await set_shop_setting("shop_info", message.text)
    await state.clear()
    await message.answer(f"<b><tg-emoji emoji-id='{EMOJI['check']}'>✅</tg-emoji> Описание магазина обновлено!</b>", parse_mode="HTML", reply_markup=admin_back())


# ==================== Cancel State Handler ====================
@router.callback_query(F.data.in_(["admin_panel", "admin_media", "admin_categories", "admin_products", "addprod", "admin_settings"]))
async def cancel_state(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()


# ==================== Main ====================
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)

    print("\033[35m" + "═" * 40)
    print("  🤖 Vest Creator Bot")
    print("═" * 40 + "\033[0m")
    print("🚀 Бот запущен!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
