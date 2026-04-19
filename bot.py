import asyncio
import logging
import os
import asyncpg
import aiohttp
import ssl
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ContentType, BotCommand, BotCommandScopeChat
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_IDS = [7973988177]
SUPPORT_USERNAME = "@VestSupport"
SHOP_NAME = "Vest Creator"
SBP_PHONE = "+79818376180"
SBP_BANK = "ЮМАНИ"
USDT_RATE = 90.0

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле!")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не найден в .env файле!")

# ==================== PREMIUM EMOJI IDs ====================
E_SETTINGS = "5870982283724328568"
E_PROFILE = "5870994129244131212"
E_PEOPLE = "5870772616305839506"
E_USER_CHECK = "5891207662678317861"
E_FILE = "5870528606328852614"
E_STATS = "5870921681735781843"
E_HOUSE = "5873147866364514353"
E_MEGAPHONE = "6039422865189638057"
E_CHECK = "5870633910337015697"
E_CROSS = "5870657884844462243"
E_PENCIL = "5870676941614354370"
E_TRASH = "5870875489362513438"
E_DOWN = "5893057118545646106"
E_INFO = "6028435952299413210"
E_ADMIN = "6030400221232501136"
E_SEND = "5963103826075456248"
E_MONEY = "5904462880941545555"
E_BACK = "5774022692642492953"
E_SHOP = "5778672437122045013"
E_SUPPORT = "6039486778597970865"
E_CATALOG = "5884479287171485878"
E_PURCHASES = "5870528606328852614"
E_CATEGORY = "5870528606328852614"
E_PRODUCT = "5884479287171485878"
E_PRICE = "5904462880941545555"
E_BUY = "5769126056262898415"
E_PAY = "5890848474563352982"
E_ADD = "5870633910337015697"
E_EDIT = "5870676941614354370"
E_WALLET = "5769126056262898415"
E_CALENDAR = "5890937706803894250"
E_WRITE = "5870753782874246579"
E_MEDIA = "6035128606563241721"
E_BOX = "5884479287171485878"
E_FONT = "5870801517140775623"
E_RUB = "5904462880941545555"
E_CRYPTO = "5260752406890711732"
E_SBP = "5890848474563352982"
E_SCREENSHOT = "6035128606563241721"
E_EYE = "6037397706505195857"
E_GIFT = "6032644646587338669"
E_CLOCK = "5983150113483134607"
E_CARD = "5769126056262898415"
E_BANK = "5879814368572478751"
E_ID = "6028435952299413210"
E_SEARCH = "6037397706505195857"

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

db_pool = None

# ==================== FSM States ====================
class AdminStates(StatesGroup):
    broadcast_text = State()
    set_media_file = State()
    add_category_name = State()
    add_product_name = State()
    add_product_desc = State()
    add_product_price = State()
    add_product_type = State()
    add_product_content = State()
    add_product_files = State()
    edit_shop_info = State()

class UserStates(StatesGroup):
    waiting_sbp_screenshot = State()

# ==================== Database ====================
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    
    async with db_pool.acquire() as conn:
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
                product_type TEXT DEFAULT 'text',
                content TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS product_files (
                id SERIAL PRIMARY KEY,
                product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
                file_id TEXT NOT NULL,
                file_name TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
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
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_id INTEGER,
                invoice_id TEXT,
                amount REAL,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                payment_method TEXT DEFAULT 'cryptobot'
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sbp_payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_id INTEGER,
                amount_rub REAL,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                reviewed_by BIGINT,
                reviewed_at TEXT
            )
        ''')

# ==================== DB Functions ====================
async def add_user(user: types.User):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (user_id, username, first_name, registered_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO NOTHING
        ''', user.id, user.username, user.first_name, datetime.now().isoformat())

async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)

async def get_stats():
    async with db_pool.acquire() as conn:
        users_count = await conn.fetchval('SELECT COUNT(*) FROM users')
        purchases_count = await conn.fetchval('SELECT COUNT(*) FROM purchases')
        total_revenue = await conn.fetchval('SELECT COALESCE(SUM(price), 0) FROM purchases')
        products_count = await conn.fetchval('SELECT COUNT(*) FROM products WHERE is_active = 1')
    return users_count, purchases_count, total_revenue, products_count

async def get_categories():
    async with db_pool.acquire() as conn:
        return await conn.fetch('SELECT * FROM categories ORDER BY id')

async def add_category(name: str):
    async with db_pool.acquire() as conn:
        await conn.execute('INSERT INTO categories (name) VALUES ($1)', name)

async def delete_category(cat_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM categories WHERE id = $1', cat_id)

async def get_products_by_category(category_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetch('SELECT * FROM products WHERE category_id = $1 AND is_active = 1', category_id)

async def get_product(product_id: int):
    async with db_pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
        if product:
            files = await conn.fetch('SELECT * FROM product_files WHERE product_id = $1', product_id)
            product = dict(product)
            product['files'] = files
        return product

async def add_product(category_id, name, description, price, product_type, content=None):
    async with db_pool.acquire() as conn:
        product_id = await conn.fetchval('''
            INSERT INTO products (category_id, name, description, price, product_type, content, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        ''', category_id, name, description, price, product_type, content, datetime.now().isoformat())
        return product_id

async def add_product_file(product_id: int, file_id: str, file_name: str = None):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO product_files (product_id, file_id, file_name)
            VALUES ($1, $2, $3)
        ''', product_id, file_id, file_name)

async def delete_product(product_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE products SET is_active = 0 WHERE id = $1', product_id)

async def add_purchase(user_id: int, product_id: int, price: float):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO purchases (user_id, product_id, price, purchased_at)
            VALUES ($1, $2, $3, $4)
        ''', user_id, product_id, price, datetime.now().isoformat())
        await conn.execute('''
            UPDATE users SET total_purchases = total_purchases + 1, 
            total_spent = total_spent + $1 WHERE user_id = $2
        ''', price, user_id)

async def get_user_purchases(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetch('''
            SELECT p.*, pr.name as product_name FROM purchases p 
            JOIN products pr ON p.product_id = pr.id WHERE p.user_id = $1 ORDER BY p.purchased_at DESC LIMIT 10
        ''', user_id)

async def set_media(key: str, media_type: str, file_id: str):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO media_settings (key, media_type, file_id) VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET media_type = $2, file_id = $3
        ''', key, media_type, file_id)

async def get_media(key: str):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM media_settings WHERE key = $1', key)

async def get_shop_setting(key: str, default: str = ""):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT value FROM shop_settings WHERE key = $1', key)
        return row['value'] if row else default

async def set_shop_setting(key: str, value: str):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO shop_settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2
        ''', key, value)

async def save_payment(user_id: int, product_id: int, invoice_id: str, amount: float, method: str = 'cryptobot'):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO payments (user_id, product_id, invoice_id, amount, created_at, payment_method)
            VALUES ($1, $2, $3, $4, $5, $6)
        ''', user_id, product_id, invoice_id, amount, datetime.now().isoformat(), method)

async def update_payment_status(invoice_id: str, status: str):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE payments SET status = $1 WHERE invoice_id = $2', status, invoice_id)

async def get_payment(invoice_id: str):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM payments WHERE invoice_id = $1', invoice_id)

async def get_all_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id FROM users')
        return [row['user_id'] for row in rows]

async def delete_media(key: str):
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM media_settings WHERE key = $1', key)

async def save_sbp_payment(user_id: int, product_id: int, amount_rub: float, screenshot_file_id: str):
    async with db_pool.acquire() as conn:
        payment_id = await conn.fetchval('''
            INSERT INTO sbp_payments (user_id, product_id, amount_rub, screenshot_file_id, created_at)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
        ''', user_id, product_id, amount_rub, screenshot_file_id, datetime.now().isoformat())
        return payment_id

async def get_pending_sbp_payments():
    async with db_pool.acquire() as conn:
        return await conn.fetch('''
            SELECT sp.*, p.name as product_name, u.username, u.first_name 
            FROM sbp_payments sp
            JOIN products p ON sp.product_id = p.id
            JOIN users u ON sp.user_id = u.user_id
            WHERE sp.status = 'pending'
            ORDER BY sp.created_at DESC
        ''')

async def get_sbp_payment(payment_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('''
            SELECT sp.*, p.name as product_name, p.product_type, p.content,
                   u.username, u.first_name, u.user_id as buyer_id
            FROM sbp_payments sp
            JOIN products p ON sp.product_id = p.id
            JOIN users u ON sp.user_id = u.user_id
            WHERE sp.id = $1
        ''', payment_id)

async def update_sbp_payment_status(payment_id: int, status: str, admin_id: int = None):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            UPDATE sbp_payments 
            SET status = $1, reviewed_by = $2, reviewed_at = $3
            WHERE id = $4
        ''', status, admin_id, datetime.now().isoformat(), payment_id)

# ==================== CryptoBot API ====================
async def create_invoice(amount_usdt: float, description: str, payload: str):
    if not CRYPTOBOT_TOKEN:
        return None
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

async def check_invoice(invoice_id: str):
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
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Купить"),
        KeyboardButton(text="Мой профиль")
    )
    builder.row(
        KeyboardButton(text="О шопе"),
        KeyboardButton(text="Поддержка")
    )
    keyboard = builder.export()
    keyboard[0][0].icon_custom_emoji_id = E_SHOP
    keyboard[0][1].icon_custom_emoji_id = E_PROFILE
    keyboard[1][0].icon_custom_emoji_id = E_INFO
    keyboard[1][1].icon_custom_emoji_id = E_SUPPORT
    return keyboard

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text=f"Медиа", callback_data="admin_media")],
        [InlineKeyboardButton(text=f"Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text=f"Товары", callback_data="admin_products")],
        [InlineKeyboardButton(text=f"Категории", callback_data="admin_categories")],
        [InlineKeyboardButton(text=f"СБП платежи", callback_data="admin_sbp_payments")],
        [InlineKeyboardButton(text=f"Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton(text=f"◁ Назад", callback_data="main")]
    ])

def admin_back():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_panel")]
    ])

def back_button(callback_data: str = "main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"◁ Назад", callback_data=callback_data)]
    ])

# ==================== Helper Functions ====================
async def send_with_media(chat_id: int, text: str, media_key: str, reply_markup=None):
    media = await get_media(media_key)
    if media:
        if media['media_type'] == "photo":
            await bot.send_photo(chat_id, media['file_id'], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media['media_type'] == "video":
            await bot.send_video(chat_id, media['file_id'], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media['media_type'] == "animation":
            await bot.send_animation(chat_id, media['file_id'], caption=text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)

async def set_commands(user_id: int):
    commands = [BotCommand(command="start", description="Старт")]
    if user_id in ADMIN_IDS:
        commands.append(BotCommand(command="admin", description="Админ панель"))
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))

async def deliver_product(user_id: int, product: dict):
    if product['product_type'] == 'text':
        text = f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Товар оплачен!</b>\n\n<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> <b>{product['name']}</b>\n\n<blockquote>{product['content']}</blockquote>"
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=back_button("shop"))
    else:
        files = product.get('files', [])
        if files:
            for file in files:
                try:
                    await bot.send_document(user_id, file['file_id'], caption=f"<tg-emoji emoji-id='{E_FILE}'>📎</tg-emoji> {product['name']}")
                except:
                    pass
            await bot.send_message(user_id, f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Товар успешно выдан!</b>", parse_mode="HTML", reply_markup=back_button("shop"))
        else:
            await bot.send_message(user_id, f"<tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> Ошибка: файлы не найдены")

# ==================== Handlers ====================
@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user)
    await set_commands(message.from_user.id)
    text = f"<b><tg-emoji emoji-id='{E_HOUSE}'>🏪</tg-emoji> {SHOP_NAME}</b>\n\n<blockquote><tg-emoji emoji-id='{E_DOWN}'>👇</tg-emoji> Выберите действие:</blockquote>"
    await message.answer(text, parse_mode="HTML", reply_markup=main_keyboard())

@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await message.answer(
        f"<blockquote><tg-emoji emoji-id='{E_ADMIN}'>🎩</tg-emoji> <b>Админ панель</b></blockquote>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )

@router.callback_query(F.data == "main")
async def cb_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = f"<b><tg-emoji emoji-id='{E_HOUSE}'>🏪</tg-emoji> {SHOP_NAME}</b>\n\n<blockquote><tg-emoji emoji-id='{E_DOWN}'>👇</tg-emoji> Выберите действие:</blockquote>"
    try:
        await callback.message.delete()
    except:
        pass
    await bot.send_message(callback.from_user.id, text, parse_mode="HTML", reply_markup=main_keyboard())
    await callback.answer()

@router.message(F.text == "Купить")
async def text_shop(message: types.Message):
    categories = await get_categories()
    if not categories:
        await message.answer("Категории пока не добавлены")
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(
            text=f"<tg-emoji emoji-id='{E_CATEGORY}'>📂</tg-emoji> {cat['name']}",
            callback_data=f"cat_{cat['id']}"
        )])
    keyboard.append([InlineKeyboardButton(text=f"◁ Назад", callback_data="main")])

    text = f"<b><tg-emoji emoji-id='{E_CATALOG}'>🛒</tg-emoji> Каталог товаров</b>\n\n<blockquote><tg-emoji emoji-id='{E_DOWN}'>👇</tg-emoji> Выберите категорию:</blockquote>"
    await send_with_media(message.chat.id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=keyboard))

@router.message(F.text == "Мой профиль")
async def text_profile(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await add_user(message.from_user)
        user = await get_user(message.from_user.id)
    
    purchases = await get_user_purchases(message.from_user.id)

    text = f"<b><tg-emoji emoji-id='{E_PROFILE}'>👤</tg-emoji> Мой профиль</b>\n\n"
    text += f"<tg-emoji emoji-id='{E_ID}'>🆔</tg-emoji> <b>ID:</b> <code>{message.from_user.id}</code>\n"
    text += f"<tg-emoji emoji-id='{E_BOX}'>🛒</tg-emoji> <b>Покупок:</b> {user['total_purchases']}\n"
    text += f"<tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> <b>Потрачено:</b> {user['total_spent']:.0f} ₽\n"
    text += f"<tg-emoji emoji-id='{E_CALENDAR}'>📅</tg-emoji> <b>Регистрация:</b> {user['registered_at'][:10]}\n"

    if purchases:
        text += f"\n<b><tg-emoji emoji-id='{E_PURCHASES}'>📋</tg-emoji> Последние покупки:</b>\n"
        for p in purchases[:5]:
            text += f"• {p['product_name']} — {p['price']:.0f} ₽\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_PURCHASES}'>📜</tg-emoji> Мои покупки", callback_data="my_purchases")]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@router.message(F.text == "О шопе")
async def text_about(message: types.Message):
    info = await get_shop_setting("shop_info", "Информация о магазине не заполнена.")
    text = f"<b><tg-emoji emoji-id='{E_INFO}'>🏬</tg-emoji> О шопе</b>\n\n<blockquote>{info}</blockquote>"
    await send_with_media(message.chat.id, text, "about_menu", None)

@router.message(F.text == "Поддержка")
async def text_support(message: types.Message):
    text = f"<b><tg-emoji emoji-id='{E_SUPPORT}'>🛟</tg-emoji> Поддержка</b>\n\n<blockquote>По всем вопросам обращайтесь: {SUPPORT_USERNAME}</blockquote>"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_WRITE}'>✉️</tg-emoji> Написать", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}")]
    ])
    await send_with_media(message.chat.id, text, "support_menu", keyboard)

# ==================== Shop Callbacks ====================
@router.callback_query(F.data == "shop")
async def cb_shop(callback: types.CallbackQuery):
    categories = await get_categories()
    if not categories:
        await callback.answer("Категории пока не добавлены", show_alert=True)
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(
            text=f"<tg-emoji emoji-id='{E_CATEGORY}'>📂</tg-emoji> {cat['name']}",
            callback_data=f"cat_{cat['id']}"
        )])
    keyboard.append([InlineKeyboardButton(text=f"◁ Назад", callback_data="main")])

    text = f"<b><tg-emoji emoji-id='{E_CATALOG}'>🛒</tg-emoji> Каталог товаров</b>\n\n<blockquote><tg-emoji emoji-id='{E_DOWN}'>👇</tg-emoji> Выберите категорию:</blockquote>"
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
            text=f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> {prod['name']} — {prod['price']:.0f} ₽",
            callback_data=f"prod_{prod['id']}"
        )])
    keyboard.append([InlineKeyboardButton(text=f"◁ Назад", callback_data="shop")])

    await callback.message.edit_text(
        f"<blockquote><tg-emoji emoji-id='{E_DOWN}'>👇</tg-emoji> Выберите товар:</blockquote>",
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

    text = f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> {product['name']}</b>\n\n"
    text += f"<blockquote>{product['description']}</blockquote>\n\n"
    text += f"<b><tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> Цена:</b> {product['price']:.0f} ₽"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CRYPTO}'>💳</tg-emoji> Купить CryptoBot", callback_data=f"buycrypto_{prod_id}")],
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_SBP}'>🏦</tg-emoji> Купить СБП", callback_data=f"buysbp_{prod_id}")],
        [InlineKeyboardButton(text=f"◁ Назад", callback_data=f"cat_{product['category_id']}")]
    ])

    try:
        await callback.message.delete()
    except:
        pass
    await send_with_media(callback.from_user.id, text, f"product_{prod_id}", keyboard)
    await callback.answer()

# ==================== CryptoBot Payment ====================
@router.callback_query(F.data.startswith("buycrypto_"))
async def cb_buy_crypto(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    price_rub = product['price']
    price_usdt = price_rub / USDT_RATE

    invoice = await create_invoice(
        amount=price_usdt,
        description=f"Покупка: {product['name']}",
        payload=f"{callback.from_user.id}:{prod_id}"
    )

    if not invoice:
        await callback.answer("Ошибка создания платежа. Попробуйте позже.", show_alert=True)
        return

    await save_payment(callback.from_user.id, prod_id, str(invoice['invoice_id']), price_rub, 'cryptobot')

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_PAY}'>💳</tg-emoji> Оплатить", url=invoice['pay_url'])],
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Проверить оплату", callback_data=f"check_{invoice['invoice_id']}")],
        [InlineKeyboardButton(text=f"◁ Отмена", callback_data=f"prod_{prod_id}")]
    ])

    text = f"<b><tg-emoji emoji-id='{E_CRYPTO}'>💳</tg-emoji> Оплата CryptoBot</b>\n\n"
    text += f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> <b>Товар:</b> {product['name']}\n"
    text += f"<tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> <b>Сумма:</b> {price_rub:.0f} ₽ (~{price_usdt:.2f} USDT)\n\n"
    text += "<blockquote>Нажмите «Оплатить» и после оплаты нажмите «Проверить оплату»</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("check_"))
async def cb_check_payment(callback: types.CallbackQuery):
    invoice_id = callback.data.split("_")[1]
    invoice = await check_invoice(invoice_id)

    if not invoice:
        await callback.answer("Ошибка проверки платежа", show_alert=True)
        return

    if invoice['status'] == 'paid':
        payment = await get_payment(invoice_id)
        if payment and payment['status'] == 'pending':
            await update_payment_status(invoice_id, 'paid')
            product = await get_product(payment['product_id'])
            await add_purchase(callback.from_user.id, payment['product_id'], payment['amount'])

            await deliver_product(callback.from_user.id, product)

            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"<b><tg-emoji emoji-id='{E_MONEY}'>💰</tg-emoji> Новая покупка!</b>\n\n"
                        f"<tg-emoji emoji-id='{E_USER_CHECK}'>👤</tg-emoji> Покупатель: @{callback.from_user.username or 'Без юзернейма'}\n"
                        f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Товар: {product['name']}\n"
                        f"<tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> Сумма: {payment['amount']:.0f} ₽\n"
                        f"<tg-emoji emoji-id='{E_CRYPTO}'>💳</tg-emoji> Метод: CryptoBot",
                        parse_mode="HTML"
                    )
                except:
                    pass
        else:
            await callback.answer("Товар уже выдан!", show_alert=True)
    else:
        await callback.answer("Оплата не найдена. Попробуйте позже.", show_alert=True)

# ==================== SBP Payment ====================
@router.callback_query(F.data.startswith("buysbp_"))
async def cb_buy_sbp(callback: types.CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    await state.update_data(sbp_product_id=prod_id, sbp_price=product['price'])
    await state.set_state(UserStates.waiting_sbp_screenshot)

    text = f"<b><tg-emoji emoji-id='{E_BANK}'>🏦</tg-emoji> Оплата через СБП</b>\n\n"
    text += f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> <b>Товар:</b> {product['name']}\n"
    text += f"<tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> <b>Сумма к оплате:</b> {product['price']:.0f} ₽\n\n"
    text += f"<tg-emoji emoji-id='{E_CARD}'>📱</tg-emoji> <b>Номер для перевода (СБП):</b>\n<code>{SBP_PHONE}</code>\n"
    text += f"<tg-emoji emoji-id='{E_BANK}'>🏛</tg-emoji> <b>Банк:</b> {SBP_BANK}\n\n"
    text += "<blockquote>После оплаты нажмите кнопку «Я оплатил» и отправьте скриншот перевода</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Я оплатил", callback_data="sbp_paid")],
        [InlineKeyboardButton(text=f"◁ Назад", callback_data=f"prod_{prod_id}")]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "sbp_paid")
async def cb_sbp_paid(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_SCREENSHOT}'>📸</tg-emoji> Отправьте скриншот оплаты</b>\n\n"
        "<blockquote>Пожалуйста, отправьте скриншот подтверждающий перевод</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"◁ Отмена", callback_data="shop")]
        ])
    )
    await callback.answer()

@router.message(UserStates.waiting_sbp_screenshot, F.photo)
async def process_sbp_screenshot(message: types.Message, state: FSMContext):
    data = await state.get_data()
    prod_id = data.get('sbp_product_id')
    price = data.get('sbp_price')

    await state.clear()

    screenshot_file_id = message.photo[-1].file_id
    payment_id = await save_sbp_payment(message.from_user.id, prod_id, price, screenshot_file_id)

    await message.answer(
        f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Скриншот получен!</b>\n\n"
        "<blockquote>Администратор проверит оплату в ближайшее время. Ожидайте.</blockquote>",
        parse_mode="HTML",
        reply_markup=back_button("shop")
    )

    for admin_id in ADMIN_IDS:
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Одобрить", callback_data=f"approve_sbp_{payment_id}")],
                [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> Отклонить", callback_data=f"reject_sbp_{payment_id}")]
            ])
            await bot.send_photo(
                admin_id,
                screenshot_file_id,
                caption=f"<b><tg-emoji emoji-id='{E_BANK}'>🏦</tg-emoji> Новая заявка СБП #{payment_id}</b>\n\n"
                        f"<tg-emoji emoji-id='{E_USER_CHECK}'>👤</tg-emoji> Покупатель: @{message.from_user.username or 'Без юзернейма'}\n"
                        f"<tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> Сумма: {price:.0f} ₽",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except:
            pass

# ==================== Admin SBP Review ====================
@router.callback_query(F.data == "admin_sbp_payments")
async def cb_admin_sbp_payments(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    payments = await get_pending_sbp_payments()

    if not payments:
        await callback.answer("Нет ожидающих платежей", show_alert=True)
        return

    text = f"<b><tg-emoji emoji-id='{E_SBP}'>💳</tg-emoji> Ожидающие СБП платежи</b>\n\n"
    for p in payments:
        text += f"#{p['id']} | {p['amount_rub']:.0f} ₽ | @{p['username'] or p['first_name']}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_SEARCH}'>🔍</tg-emoji> Проверить #{p['id']}", callback_data=f"review_sbp_{p['id']}")]
        for p in payments
    ] + [[InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_panel")]])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("review_sbp_"))
async def cb_review_sbp(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    payment_id = int(callback.data.split("_")[2])
    payment = await get_sbp_payment(payment_id)

    if not payment:
        await callback.answer("Платеж не найден", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Одобрить", callback_data=f"approve_sbp_{payment_id}")],
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> Отклонить", callback_data=f"reject_sbp_{payment_id}")],
        [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_sbp_payments")]
    ])

    await bot.send_photo(
        callback.from_user.id,
        payment['screenshot_file_id'],
        caption=f"<b>Заявка СБП #{payment_id}</b>\n\n"
                f"<tg-emoji emoji-id='{E_USER_CHECK}'>👤</tg-emoji> Покупатель: @{payment['username'] or payment['first_name']}\n"
                f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Товар: {payment['product_name']}\n"
                f"<tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> Сумма: {payment['amount_rub']:.0f} ₽\n"
                f"<tg-emoji emoji-id='{E_CALENDAR}'>📅</tg-emoji> Дата: {payment['created_at'][:19]}",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data.startswith("approve_sbp_"))
async def cb_approve_sbp(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    payment_id = int(callback.data.split("_")[2])
    payment = await get_sbp_payment(payment_id)

    if not payment or payment['status'] != 'pending':
        await callback.answer("Платеж уже обработан", show_alert=True)
        return

    await update_sbp_payment_status(payment_id, 'approved', callback.from_user.id)

    product = await get_product(payment['product_id'])
    await add_purchase(payment['buyer_id'], payment['product_id'], payment['amount_rub'])

    await deliver_product(payment['buyer_id'], product)

    await bot.send_message(
        payment['buyer_id'],
        f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Ваш платёж одобрен!</b>\n\nТовар выдан выше.",
        parse_mode="HTML"
    )

    await callback.message.edit_caption(
        caption=f"{callback.message.caption}\n\n<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> ОДОБРЕНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Платёж одобрен, товар выдан")

@router.callback_query(F.data.startswith("reject_sbp_"))
async def cb_reject_sbp(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    payment_id = int(callback.data.split("_")[2])
    payment = await get_sbp_payment(payment_id)

    if not payment or payment['status'] != 'pending':
        await callback.answer("Платеж уже обработан", show_alert=True)
        return

    await update_sbp_payment_status(payment_id, 'rejected', callback.from_user.id)

    await bot.send_message(
        payment['buyer_id'],
        f"<b><tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> Ваш платёж отклонён.</b>\n\nСвяжитесь с поддержкой: {SUPPORT_USERNAME}",
        parse_mode="HTML"
    )

    await callback.message.edit_caption(
        caption=f"{callback.message.caption}\n\n<b><tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> ОТКЛОНЕНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Платёж отклонён")

@router.callback_query(F.data == "my_purchases")
async def cb_my_purchases(callback: types.CallbackQuery):
    purchases = await get_user_purchases(callback.from_user.id)

    if not purchases:
        await callback.answer("У вас пока нет покупок", show_alert=True)
        return

    text = f"<b><tg-emoji emoji-id='{E_PURCHASES}'>📜</tg-emoji> Мои покупки</b>\n\n"
    for p in purchases:
        text += f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> {p['product_name']} — {p['price']:.0f} ₽ ({p['purchased_at'][:10]})\n"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button("main"))
    await callback.answer()

# ==================== Admin Handlers ====================
@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        f"<blockquote><tg-emoji emoji-id='{E_ADMIN}'>🎩</tg-emoji> <b>Админ панель</b></blockquote>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    users, purchases, revenue, products = await get_stats()

    text = f"<b><tg-emoji emoji-id='{E_STATS}'>📊</tg-emoji> Статистика</b>\n\n"
    text += f"<tg-emoji emoji-id='{E_PEOPLE}'>👥</tg-emoji> <b>Пользователей:</b> {users}\n"
    text += f"<tg-emoji emoji-id='{E_BOX}'>🛒</tg-emoji> <b>Покупок:</b> {purchases}\n"
    text += f"<tg-emoji emoji-id='{E_RUB}'>💵</tg-emoji> <b>Выручка:</b> {revenue:.0f} ₽\n"
    text += f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> <b>Товаров:</b> {products}"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()

@router.callback_query(F.data == "admin_media")
async def cb_admin_media(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_HOUSE}'>🏠</tg-emoji> Главное меню", callback_data="setmedia_main_menu")],
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_SHOP}'>🛒</tg-emoji> Меню магазина", callback_data="setmedia_shop_menu")],
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_INFO}'>🏬</tg-emoji> О шопе", callback_data="setmedia_about_menu")],
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_SUPPORT}'>🛟</tg-emoji> Поддержка", callback_data="setmedia_support_menu")],
        [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_MEDIA}'>🖼</tg-emoji> Настройка медиа</b>\n\n<blockquote>Выберите раздел для установки медиа:</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data.startswith("setmedia_"))
async def cb_setmedia(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    media_key = callback.data.replace("setmedia_", "")
    await state.update_data(media_key=media_key)
    await state.set_state(AdminStates.set_media_file)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_TRASH}'>🗑</tg-emoji> Удалить медиа", callback_data=f"delmedia_{media_key}")],
        [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_media")]
    ])

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_MEDIA}'>🖼</tg-emoji> Установка медиа</b>\n\n<blockquote>Отправьте фото, видео или GIF для этого раздела:</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
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

@router.message(AdminStates.set_media_file, F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION]))
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
    await message.answer(f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Медиа успешно установлено!</b>", parse_mode="HTML", reply_markup=admin_back())

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.broadcast_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_MEGAPHONE}'>📨</tg-emoji> Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF для рассылки:</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@router.message(AdminStates.broadcast_text)
async def process_broadcast(message: types.Message, state: FSMContext):
    await state.clear()
    users = await get_all_users()

    success = 0
    failed = 0

    status_msg = await message.answer(f"<tg-emoji emoji-id='{E_SEND}'>📤</tg-emoji> Рассылка начата...", parse_mode="HTML")

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.animation:
                await bot.send_animation(user_id, message.animation.file_id, caption=message.caption or "", parse_mode="HTML")
            else:
                await bot.send_message(user_id, message.text, parse_mode="HTML")
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Рассылка завершена!</b>\n\n"
        f"<tg-emoji emoji-id='{E_SEND}'>📤</tg-emoji> Успешно: {success}\n"
        f"<tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> Ошибок: {failed}",
        parse_mode="HTML",
        reply_markup=admin_back()
    )

@router.callback_query(F.data == "admin_categories")
async def cb_admin_categories(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()

    keyboard = []
    for cat in categories:
        keyboard.append([
            InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CATEGORY}'>📂</tg-emoji> {cat['name']}", callback_data=f"editcat_{cat['id']}"),
            InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_TRASH}'>🗑</tg-emoji>", callback_data=f"delcat_{cat['id']}")
        ])
    keyboard.append([InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_ADD}'>➕</tg-emoji> Добавить категорию", callback_data="addcat")])
    keyboard.append([InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_panel")])

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_CATEGORY}'>📁</tg-emoji> Категории</b>\n\n<blockquote>Управление категориями товаров:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@router.callback_query(F.data == "addcat")
async def cb_addcat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.add_category_name)

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_CATEGORY}'>📁</tg-emoji> Новая категория</b>\n\n<blockquote>Введите название категории:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_categories")]
        ])
    )
    await callback.answer()

@router.message(AdminStates.add_category_name)
async def process_category_name(message: types.Message, state: FSMContext):
    await add_category(message.text)
    await state.clear()
    await message.answer(f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Категория добавлена!</b>", parse_mode="HTML", reply_markup=admin_back())

@router.callback_query(F.data.startswith("delcat_"))
async def cb_delcat(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    await delete_category(cat_id)
    await callback.answer("Категория удалена", show_alert=True)
    await cb_admin_categories(callback)

@router.callback_query(F.data == "admin_products")
async def cb_admin_products(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CATEGORY}'>📂</tg-emoji> {cat['name']}", callback_data=f"admincat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_ADD}'>➕</tg-emoji> Добавить товар", callback_data="addprod")])
    keyboard.append([InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_panel")])

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Товары</b>\n\n<blockquote>Выберите категорию для просмотра товаров:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
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
            InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> {prod['name']} — {prod['price']:.0f} ₽", callback_data=f"viewprod_{prod['id']}"),
            InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_TRASH}'>🗑</tg-emoji>", callback_data=f"delprod_{prod['id']}")
        ])
    keyboard.append([InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_products")])

    await callback.message.edit_text(
        f"<blockquote><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Товары в категории:</blockquote>",
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
    await cb_admin_products(callback)

# ==================== Add Product ====================
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
        keyboard.append([InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CATEGORY}'>📂</tg-emoji> {cat['name']}", callback_data=f"newprodcat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_products")])

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Новый товар</b>\n\n<blockquote>Выберите категорию для товара:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("newprodcat_"))
async def cb_newprodcat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    await state.update_data(category_id=cat_id)
    await state.set_state(AdminStates.add_product_name)

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите название товара:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"◁ Назад", callback_data="addprod")]
        ])
    )
    await callback.answer()

@router.message(AdminStates.add_product_name)
async def process_product_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.add_product_desc)

    await message.answer(
        f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите описание товара:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"◁ Назад", callback_data="addprod")]
        ])
    )

@router.message(AdminStates.add_product_desc)
async def process_product_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AdminStates.add_product_price)

    await message.answer(
        f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите цену в рублях:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"◁ Назад", callback_data="addprod")]
        ])
    )

@router.message(AdminStates.add_product_price)
async def process_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        await state.update_data(price=price)
        await state.set_state(AdminStates.add_product_type)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_FONT}'>📝</tg-emoji> Текстовый", callback_data="prodtype_text")],
            [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_FILE}'>📎</tg-emoji> Файловый", callback_data="prodtype_file")],
            [InlineKeyboardButton(text=f"◁ Назад", callback_data="addprod")]
        ])

        await message.answer(
            f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Новый товар</b>\n\n<blockquote>Выберите тип товара:</blockquote>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except ValueError:
        await message.answer(f"<tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> Введите корректную цену (число)")

@router.callback_query(F.data.startswith("prodtype_"), AdminStates.add_product_type)
async def cb_prodtype(callback: types.CallbackQuery, state: FSMContext):
    prod_type = callback.data.split("_")[1]
    await state.update_data(product_type=prod_type)

    if prod_type == "text":
        await state.set_state(AdminStates.add_product_content)
        await callback.message.edit_text(
            f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите текстовый контент товара:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"◁ Назад", callback_data="addprod")]
            ])
        )
    else:
        await state.set_state(AdminStates.add_product_files)
        await callback.message.edit_text(
            f"<b><tg-emoji emoji-id='{E_PRODUCT}'>📦</tg-emoji> Новый товар</b>\n\n<blockquote>Отправьте файлы товара (до 10 файлов). После отправки нажмите «Готово»</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Готово", callback_data="files_done")],
                [InlineKeyboardButton(text=f"◁ Назад", callback_data="addprod")]
            ])
        )
        await state.update_data(product_files=[])
    await callback.answer()

@router.message(AdminStates.add_product_content)
async def process_product_content(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        'text',
        content=message.text
    )
    await state.clear()
    await message.answer(f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Товар успешно добавлен!</b>", parse_mode="HTML", reply_markup=admin_back())

@router.message(AdminStates.add_product_files, F.document)
async def process_product_files(message: types.Message, state: FSMContext):
    data = await state.get_data()
    files = data.get('product_files', [])
    
    if len(files) >= 10:
        await message.answer(f"<tg-emoji emoji-id='{E_CROSS}'>❌</tg-emoji> Максимум 10 файлов!")
        return
    
    files.append({
        'file_id': message.document.file_id,
        'file_name': message.document.file_name
    })
    await state.update_data(product_files=files)
    
    await message.answer(
        f"<tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Файл добавлен! ({len(files)}/10)\n"
        f"Отправьте ещё или нажмите «Готово»"
    )

@router.callback_query(F.data == "files_done", AdminStates.add_product_files)
async def cb_files_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    files = data.get('product_files', [])
    
    if not files:
        await callback.answer("Добавьте хотя бы один файл!", show_alert=True)
        return
    
    product_id = await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        'file'
    )
    
    for file in files:
        await add_product_file(product_id, file['file_id'], file['file_name'])
    
    await state.clear()
    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Товар успешно добавлен! ({len(files)} файлов)</b>",
        parse_mode="HTML",
        reply_markup=admin_back()
    )
    await callback.answer()

# ==================== Admin Settings ====================
@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"<tg-emoji emoji-id='{E_EDIT}'>📝</tg-emoji> Изменить описание магазина", callback_data="edit_shop_info")],
        [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_SETTINGS}'>⚙️</tg-emoji> Настройки</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data == "edit_shop_info")
async def cb_edit_shop_info(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.edit_shop_info)

    await callback.message.edit_text(
        f"<b><tg-emoji emoji-id='{E_PENCIL}'>📝</tg-emoji> Описание магазина</b>\n\n<blockquote>Введите новое описание магазина:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"◁ Назад", callback_data="admin_settings")]
        ])
    )
    await callback.answer()

@router.message(AdminStates.edit_shop_info)
async def process_shop_info(message: types.Message, state: FSMContext):
    await set_shop_setting("shop_info", message.text)
    await state.clear()
    await message.answer(f"<b><tg-emoji emoji-id='{E_CHECK}'>✅</tg-emoji> Описание магазина обновлено!</b>", parse_mode="HTML", reply_markup=admin_back())

# ==================== Main ====================
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    print("\033[35m" + "═" * 40)
    print("  🤖 Создатель бота: t.me/fuck_zaza")
    print("═" * 40 + "\033[0m")
    print("🚀 Бот запущен!")
    
    try:
        await dp.start_polling(bot)
    finally:
        if db_pool:
            await db_pool.close()

if __name__ == "__main__":
    asyncio.run(main())
