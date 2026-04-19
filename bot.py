import asyncio
import logging
import os
import asyncpg
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, ContentType, BotCommand, BotCommandScopeChat
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import json
from contextlib import asynccontextmanager

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
ADMIN_IDS = [7973988177]
SUPPORT_USERNAME = "@VestSupport"
SHOP_NAME = "Vest Creator"
DATABASE_URL = os.getenv("DATABASE_URL")

USDT_RATE = 90  # 1 USDT = 90 RUB
ADMIN_PAYMENT_PHONE = "+79818376180"
ADMIN_PAYMENT_BANK = "ЮМАНИ"

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ==================== Database Connection Pool ====================
db_pool = None

async def create_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

@asynccontextmanager
async def get_db():
    async with db_pool.acquire() as conn:
        yield conn

# ==================== FSM States ====================
class AdminStates(StatesGroup):
    broadcast_text = State()
    broadcast_media = State()
    set_media_select = State()
    set_media_file = State()
    add_category_name = State()
    add_product_category = State()
    add_product_name = State()
    add_product_desc = State()
    add_product_price = State()
    add_product_type = State()
    add_product_content = State()
    add_product_file = State()
    edit_shop_info = State()
    verify_payment = State()


class UserStates(StatesGroup):
    waiting_payment = State()
    waiting_sbp_screenshot = State()
    waiting_payment_amount = State()


# ==================== Database ====================
async def init_db():
    async with get_db() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
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
                price_rub REAL NOT NULL,
                product_type TEXT DEFAULT 'text',
                content TEXT,
                files JSONB DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                created_at TEXT
            )
        ''')
        
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                price_rub REAL,
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
                amount_rub REAL,
                payment_method TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                screenshot_file_id TEXT,
                admin_comment TEXT
            )
        ''')

async def add_user(user: types.User):
    async with get_db() as conn:
        await conn.execute('''
            INSERT INTO users (user_id, username, first_name, registered_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO NOTHING
        ''', user.id, user.username, user.first_name, datetime.now().isoformat())

async def get_user(user_id: int):
    async with get_db() as conn:
        return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)

async def get_stats():
    async with get_db() as conn:
        users_count = await conn.fetchval('SELECT COUNT(*) FROM users')
        purchases_count = await conn.fetchval('SELECT COUNT(*) FROM purchases')
        total_revenue = await conn.fetchval('SELECT COALESCE(SUM(price_rub), 0) FROM purchases')
        products_count = await conn.fetchval('SELECT COUNT(*) FROM products WHERE is_active = 1')
    return users_count, purchases_count, total_revenue, products_count

async def get_categories():
    async with get_db() as conn:
        return await conn.fetch('SELECT * FROM categories ORDER BY id')

async def add_category(name: str):
    async with get_db() as conn:
        await conn.execute('INSERT INTO categories (name) VALUES ($1)', name)

async def delete_category(cat_id: int):
    async with get_db() as conn:
        await conn.execute('DELETE FROM categories WHERE id = $1', cat_id)

async def get_products_by_category(category_id: int):
    async with get_db() as conn:
        return await conn.fetch('SELECT * FROM products WHERE category_id = $1 AND is_active = 1', category_id)

async def get_product(product_id: int):
    async with get_db() as conn:
        return await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)

async def add_product(category_id, name, description, price_rub, product_type, content=None, files=None):
    async with get_db() as conn:
        files_json = json.dumps(files) if files else '[]'
        await conn.execute('''
            INSERT INTO products (category_id, name, description, price_rub, product_type, content, files, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ''', category_id, name, description, price_rub, product_type, content, files_json, datetime.now().isoformat())

async def delete_product(product_id: int):
    async with get_db() as conn:
        await conn.execute('UPDATE products SET is_active = 0 WHERE id = $1', product_id)

async def add_purchase(user_id: int, product_id: int, price_rub: float):
    async with get_db() as conn:
        await conn.execute('''
            INSERT INTO purchases (user_id, product_id, price_rub, purchased_at)
            VALUES ($1, $2, $3, $4)
        ''', user_id, product_id, price_rub, datetime.now().isoformat())
        await conn.execute('''
            UPDATE users SET total_purchases = total_purchases + 1, 
            total_spent = total_spent + $1 WHERE user_id = $2
        ''', price_rub, user_id)

async def get_user_purchases(user_id: int):
    async with get_db() as conn:
        return await conn.fetch('''
            SELECT p.*, pr.name as product_name FROM purchases p 
            JOIN products pr ON p.product_id = pr.id 
            WHERE p.user_id = $1 ORDER BY p.purchased_at DESC LIMIT 10
        ''', user_id)

async def set_media(key: str, media_type: str, file_id: str):
    async with get_db() as conn:
        await conn.execute('''
            INSERT INTO media_settings (key, media_type, file_id) VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET media_type = $2, file_id = $3
        ''', key, media_type, file_id)

async def get_media(key: str):
    async with get_db() as conn:
        return await conn.fetchrow('SELECT * FROM media_settings WHERE key = $1', key)

async def get_shop_setting(key: str, default: str = ""):
    async with get_db() as conn:
        result = await conn.fetchval('SELECT value FROM shop_settings WHERE key = $1', key)
        return result if result else default

async def set_shop_setting(key: str, value: str):
    async with get_db() as conn:
        await conn.execute('''
            INSERT INTO shop_settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2
        ''', key, value)

async def save_payment(user_id: int, product_id: int, invoice_id: str, amount_rub: float, payment_method: str = "cryptobot"):
    async with get_db() as conn:
        await conn.execute('''
            INSERT INTO payments (user_id, product_id, invoice_id, amount_rub, payment_method, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
        ''', user_id, product_id, invoice_id, amount_rub, payment_method, datetime.now().isoformat())

async def update_payment_status(invoice_id: str, status: str):
    async with get_db() as conn:
        await conn.execute('UPDATE payments SET status = $1 WHERE invoice_id = $2', status, invoice_id)

async def get_payment(invoice_id: str):
    async with get_db() as conn:
        return await conn.fetchrow('SELECT * FROM payments WHERE invoice_id = $1', invoice_id)

async def get_payment_by_id(payment_id: int):
    async with get_db() as conn:
        return await conn.fetchrow('SELECT * FROM payments WHERE id = $1', payment_id)

async def get_pending_sbp_payments():
    async with get_db() as conn:
        return await conn.fetch('''
            SELECT p.*, u.username, u.first_name, pr.name as product_name 
            FROM payments p
            JOIN users u ON p.user_id = u.user_id
            JOIN products pr ON p.product_id = pr.id
            WHERE p.payment_method = 'sbp' AND p.status = 'pending'
            ORDER BY p.created_at DESC
        ''')

async def save_sbp_screenshot(payment_id: int, file_id: str):
    async with get_db() as conn:
        await conn.execute('UPDATE payments SET screenshot_file_id = $1 WHERE id = $2', file_id, payment_id)

async def get_all_users():
    async with get_db() as conn:
        rows = await conn.fetch('SELECT user_id FROM users')
        return [row['user_id'] for row in rows]

# ==================== CryptoBot API ====================
async def create_invoice(amount_rub: float, description: str, payload: str):
    amount_usdt = amount_rub / USDT_RATE
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    data = {
        "asset": "USDT",
        "amount": str(round(amount_usdt, 2)),
        "description": description,
        "payload": payload,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{(await bot.get_me()).username}"
    }
    import ssl
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
    import ssl
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
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Купить",
                    icon_custom_emoji_id="5884479287171485878"
                ),
                KeyboardButton(
                    text="Профиль",
                    icon_custom_emoji_id="5891207662678317861"
                )
            ],
            [
                KeyboardButton(
                    text="О шопе",
                    icon_custom_emoji_id="6028435952299413210"
                ),
                KeyboardButton(
                    text="Поддержка",
                    icon_custom_emoji_id="6039422865189638057"
                )
            ]
        ],
        resize_keyboard=True
    )

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Статистика",
            callback_data="admin_stats",
            icon_custom_emoji_id="5870921681735781843"
        )],
        [InlineKeyboardButton(
            text="Медиа",
            callback_data="admin_media",
            icon_custom_emoji_id="6035128606563241721"
        )],
        [InlineKeyboardButton(
            text="Рассылка",
            callback_data="admin_broadcast",
            icon_custom_emoji_id="6039422865189638057"
        )],
        [InlineKeyboardButton(
            text="Товары",
            callback_data="admin_products",
            icon_custom_emoji_id="5884479287171485878"
        )],
        [InlineKeyboardButton(
            text="Категории",
            callback_data="admin_categories",
            icon_custom_emoji_id="5870528606328852614"
        )],
        [InlineKeyboardButton(
            text="Настройки",
            callback_data="admin_settings",
            icon_custom_emoji_id="5870982283724328568"
        )],
        [InlineKeyboardButton(
            text="Проверка СБП",
            callback_data="admin_sbp_payments",
            icon_custom_emoji_id="6037397706505195857"
        )]
    ])

def admin_back():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_panel"
        )]
    ])

def payment_method_keyboard(product_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="CryptoBot",
            callback_data=f"pay_crypto_{product_id}",
            icon_custom_emoji_id="5260752406890711732"
        )],
        [InlineKeyboardButton(
            text="СБП",
            callback_data=f"pay_sbp_{product_id}",
            icon_custom_emoji_id="5904462880941545555"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data=f"prod_{product_id}"
        )]
    ])

def verify_payment_keyboard(payment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Подтвердить",
                callback_data=f"verify_approve_{payment_id}",
                icon_custom_emoji_id="5870633910337015697"
            ),
            InlineKeyboardButton(
                text="Отклонить",
                callback_data=f"verify_reject_{payment_id}",
                icon_custom_emoji_id="5870657884844462243"
            )
        ],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_sbp_payments"
        )]
    ])

def sbp_payment_keyboard(payment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Я оплатил",
            callback_data=f"sbp_paid_{payment_id}",
            icon_custom_emoji_id="5870633910337015697"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="shop"
        )]
    ])

# ==================== Helper Functions ====================
async def send_with_media(chat_id: int, text: str, media_key: str, reply_markup=None):
    media = await get_media(media_key)
    if media:
        if media["media_type"] == "photo":
            await bot.send_photo(chat_id, media["file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media["media_type"] == "video":
            await bot.send_video(chat_id, media["file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media["media_type"] == "animation":
            await bot.send_animation(chat_id, media["file_id"], caption=text, parse_mode="HTML",
                                     reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)

async def set_commands(user_id: int):
    commands = [BotCommand(command="start", description="Старт")]
    if user_id in ADMIN_IDS:
        commands.append(BotCommand(command="admin", description="Админ панель"))
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))

# ==================== Handlers ====================
@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user)
    await set_commands(message.from_user.id)
    text = f"<b>{SHOP_NAME}</b>\n\n<blockquote>Выберите действие:</blockquote>"
    await message.answer(text, parse_mode="HTML", reply_markup=main_keyboard())

@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await message.answer("<blockquote><b>Админ панель</b></blockquote>", parse_mode="HTML",
                         reply_markup=admin_keyboard())

@router.callback_query(F.data == "main")
async def cb_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = f"<b>{SHOP_NAME}</b>\n\n<blockquote>Выберите действие:</blockquote>"
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
            icon_custom_emoji_id="5870528606328852614"
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="main"
    )])

    text = "<b>Каталог товаров</b>\n\n<blockquote>Выберите категорию:</blockquote>"
    await send_with_media(message.chat.id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=keyboard))

@router.message(F.text == "Профиль")
async def text_profile(message: types.Message):
    user = await get_user(message.from_user.id)
    purchases = await get_user_purchases(message.from_user.id)

    text = f"<b>Мой профиль</b>\n\n"
    text += f"<b>ID:</b> <code>{message.from_user.id}</code>\n"
    text += f"<b>Покупок:</b> {user['total_purchases']}\n"
    text += f"<b>Потрачено:</b> {user['total_spent']:.0f}₽\n"
    text += f"<b>Регистрация:</b> {user['registered_at'][:10]}\n"

    if purchases:
        text += "\n<b>Последние покупки:</b>\n"
        for p in purchases[:5]:
            text += f"• {p['product_name']} — {p['price_rub']:.0f}₽\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Мои покупки",
            callback_data="my_purchases",
            icon_custom_emoji_id="5884479287171485878"
        )]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@router.message(F.text == "О шопе")
async def text_about(message: types.Message):
    info = await get_shop_setting("shop_info", "Информация о магазине не заполнена.")
    text = f"<b>О шопе</b>\n\n<blockquote>{info}</blockquote>"
    await send_with_media(message.chat.id, text, "about_menu", None)

@router.message(F.text == "Поддержка")
async def text_support(message: types.Message):
    text = f"<b>Поддержка</b>\n\n<blockquote>По всем вопросам обращайтесь: {SUPPORT_USERNAME}</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Написать",
            url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}",
            icon_custom_emoji_id="5870753782874246579"
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
            icon_custom_emoji_id="5870528606328852614"
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="main"
    )])

    text = "<b>Каталог товаров</b>\n\n<blockquote>Выберите категорию:</blockquote>"
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
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
            text=f"{prod['name']} — {prod['price_rub']:.0f}₽",
            callback_data=f"prod_{prod['id']}",
            icon_custom_emoji_id="5884479287171485878"
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="shop"
    )])

    await callback.message.edit_text(
        "<blockquote>Выберите товар:</blockquote>",
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

    text = f"<b>{product['name']}</b>\n\n"
    text += f"<blockquote>{product['description']}</blockquote>\n\n"
    text += f"<b>Цена:</b> {product['price_rub']:.0f}₽"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Купить",
            callback_data=f"buy_{prod_id}",
            icon_custom_emoji_id="5904462880941545555"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data=f"cat_{product['category_id']}"
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

    text = f"<b>Выберите способ оплаты</b>\n\n"
    text += f"<b>Товар:</b> {product['name']}\n"
    text += f"<b>Цена:</b> {product['price_rub']:.0f}₽"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=payment_method_keyboard(prod_id)
    )
    await callback.answer()

# ==================== CryptoBot Payment ====================
@router.callback_query(F.data.startswith("pay_crypto_"))
async def cb_pay_crypto(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[2])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    invoice = await create_invoice(
        amount_rub=product['price_rub'],
        description=f"Покупка: {product['name']}",
        payload=f"{callback.from_user.id}:{prod_id}"
    )

    if not invoice:
        await callback.answer("Ошибка создания платежа", show_alert=True)
        return

    await save_payment(callback.from_user.id, prod_id, str(invoice['invoice_id']), product['price_rub'], "cryptobot")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Оплатить",
            url=invoice['pay_url'],
            icon_custom_emoji_id="5904462880941545555"
        )],
        [InlineKeyboardButton(
            text="Проверить оплату",
            callback_data=f"check_{invoice['invoice_id']}",
            icon_custom_emoji_id="6037397706505195857"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data=f"prod_{prod_id}"
        )]
    ])

    amount_usdt = product['price_rub'] / USDT_RATE
    text = f"<b>Оплата через CryptoBot</b>\n\n"
    text += f"<b>Товар:</b> {product['name']}\n"
    text += f"<b>Сумма:</b> {product['price_rub']:.0f}₽ (~{amount_usdt:.2f} USDT)\n\n"
    text += "<blockquote>Нажмите «Оплатить» и после оплаты нажмите «Проверить оплату»</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

# ==================== SBP Payment ====================
@router.callback_query(F.data.startswith("pay_sbp_"))
async def cb_pay_sbp(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[2])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    import uuid
    invoice_id = f"sbp_{uuid.uuid4()}"
    await save_payment(callback.from_user.id, prod_id, invoice_id, product['price_rub'], "sbp")
    
    payment_record = await get_payment(invoice_id)

    text = f"<b>Оплата через СБП</b>\n\n"
    text += f"<b>Товар:</b> {product['name']}\n"
    text += f"<b>Сумма:</b> {product['price_rub']:.0f}₽\n\n"
    text += f"<b>Реквизиты для оплаты:</b>\n"
    text += f"<b>Телефон:</b> <code>{ADMIN_PAYMENT_PHONE}</code>\n"
    text += f"<b>Банк:</b> {ADMIN_PAYMENT_BANK}\n\n"
    text += "<blockquote>После оплаты нажмите кнопку «Я оплатил» и отправьте скриншот чека</blockquote>"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=sbp_payment_keyboard(payment_record['id'])
    )
    await callback.answer()

@router.callback_query(F.data.startswith("sbp_paid_"))
async def cb_sbp_paid(callback: types.CallbackQuery, state: FSMContext):
    payment_id = int(callback.data.split("_")[2])
    payment = await get_payment_by_id(payment_id)

    if not payment or payment['status'] != 'pending':
        await callback.answer("Платёж не найден или уже обработан", show_alert=True)
        return

    await state.update_data(payment_id=payment_id)
    await state.set_state(UserStates.waiting_sbp_screenshot)

    text = "<b>Отправьте скриншот оплаты</b>\n\n<blockquote>Пришлите скриншот чека об оплате. Администратор проверит его и выдаст товар.</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Отмена",
            callback_data="shop"
        )]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@router.message(UserStates.waiting_sbp_screenshot, F.photo)
async def process_sbp_screenshot(message: types.Message, state: FSMContext):
    data = await state.get_data()
    payment_id = data.get('payment_id')

    payment = await get_payment_by_id(payment_id)
    if not payment:
        await message.answer("Платёж не найден")
        await state.clear()
        return

    await save_sbp_screenshot(payment_id, message.photo[-1].file_id)
    await state.clear()

    # Notify admins
    for admin_id in ADMIN_IDS:
        try:
            product = await get_product(payment['product_id'])
            user = await get_user(payment['user_id'])
            
            text = f"<b>Новая оплата через СБП</b>\n\n"
            text += f"<b>ID платежа:</b> {payment_id}\n"
            text += f"<b>Пользователь:</b> @{user['username'] or 'Без юзернейма'}\n"
            text += f"<b>Товар:</b> {product['name']}\n"
            text += f"<b>Сумма:</b> {payment['amount_rub']:.0f}₽\n"
            
            await bot.send_photo(
                admin_id,
                message.photo[-1].file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=verify_payment_keyboard(payment_id)
            )
        except Exception as e:
            print(f"Error notifying admin {admin_id}: {e}")

    await message.answer(
        "<b>Скриншот отправлен на проверку</b>\n\n<blockquote>Администратор проверит оплату и выдаст товар. Ожидайте уведомления.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Вернуться в магазин", callback_data="shop")]
        ])
    )

# ==================== Admin SBP Verification ====================
@router.callback_query(F.data == "admin_sbp_payments")
async def cb_admin_sbp_payments(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    payments = await get_pending_sbp_payments()

    if not payments:
        await callback.answer("Нет ожидающих проверки платежей", show_alert=True)
        return

    for payment in payments:
        text = f"<b>Платёж #{payment['id']}</b>\n\n"
        text += f"<b>Пользователь:</b> @{payment['username'] or 'Без юзернейма'} ({payment['first_name']})\n"
        text += f"<b>Товар:</b> {payment['product_name']}\n"
        text += f"<b>Сумма:</b> {payment['amount_rub']:.0f}₽\n"
        text += f"<b>Дата:</b> {payment['created_at'][:19]}\n"

        if payment['screenshot_file_id']:
            await bot.send_photo(
                callback.from_user.id,
                payment['screenshot_file_id'],
                caption=text,
                parse_mode="HTML",
                reply_markup=verify_payment_keyboard(payment['id'])
            )
        else:
            await bot.send_message(
                callback.from_user.id,
                text + "\n<blockquote>Скриншот не прикреплён</blockquote>",
                parse_mode="HTML",
                reply_markup=verify_payment_keyboard(payment['id'])
            )

    await callback.answer()

@router.callback_query(F.data.startswith("verify_approve_"))
async def cb_verify_approve(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    payment_id = int(callback.data.split("_")[2])
    payment = await get_payment_by_id(payment_id)

    if not payment or payment['status'] != 'pending':
        await callback.answer("Платёж уже обработан", show_alert=True)
        return

    await update_payment_status(payment['invoice_id'], 'paid')
    product = await get_product(payment['product_id'])
    await add_purchase(payment['user_id'], payment['product_id'], payment['amount_rub'])

    # Send product to user
    text = f"<b>Оплата подтверждена!</b>\n\n<b>Товар:</b> {product['name']}\n\n"

    if product['product_type'] == 'text':
        text += f"<blockquote>{product['content']}</blockquote>"
        await bot.send_message(payment['user_id'], text, parse_mode="HTML")
    else:
        files = json.loads(product['files']) if product['files'] else []
        await bot.send_message(payment['user_id'], text, parse_mode="HTML")
        for file_id in files:
            try:
                await bot.send_document(payment['user_id'], file_id)
            except:
                pass

    # Notify user
    await bot.send_message(
        payment['user_id'],
        "<b>Ваш платёж подтверждён!</b>\n\nТовар выдан выше.",
        parse_mode="HTML"
    )

    await callback.message.delete()
    await callback.answer("Платёж подтверждён, товар выдан")

@router.callback_query(F.data.startswith("verify_reject_"))
async def cb_verify_reject(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    payment_id = int(callback.data.split("_")[2])
    await state.update_data(reject_payment_id=payment_id)
    await state.set_state(AdminStates.verify_payment)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "<b>Укажите причину отклонения:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="admin_sbp_payments")]
        ])
    )
    await callback.answer()

@router.message(AdminStates.verify_payment)
async def process_reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    payment_id = data.get('reject_payment_id')

    payment = await get_payment_by_id(payment_id)
    if payment:
        await update_payment_status(payment['invoice_id'], 'rejected')
        
        await bot.send_message(
            payment['user_id'],
            f"<b>Платёж отклонён</b>\n\nПричина: {message.text}\n\nСвяжитесь с поддержкой: {SUPPORT_USERNAME}",
            parse_mode="HTML"
        )

    await state.clear()
    await message.answer("Платёж отклонён, пользователь уведомлён", reply_markup=admin_back())

# ==================== Check Payment ====================
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
            await add_purchase(callback.from_user.id, payment['product_id'], payment['amount_rub'])

            text = f"<b>Оплата успешна!</b>\n\n<b>Товар:</b> {product['name']}\n\n"

            if product['product_type'] == 'text':
                text += f"<blockquote>{product['content']}</blockquote>"
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Вернуться в магазин", callback_data="shop")]
                ]))
            else:
                await callback.message.edit_text(text, parse_mode="HTML")
                files = json.loads(product['files']) if product['files'] else []
                for file_id in files:
                    try:
                        await bot.send_document(callback.from_user.id, file_id)
                    except:
                        pass
                await bot.send_message(
                    callback.from_user.id,
                    "Вернуться в магазин",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="В магазин", callback_data="shop")]
                    ])
                )

            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"<b>Новая покупка!</b>\n\n"
                        f"Покупатель: @{callback.from_user.username or 'Без юзернейма'}\n"
                        f"Товар: {product['name']}\n"
                        f"Сумма: {payment['amount_rub']:.0f}₽",
                        parse_mode="HTML"
                    )
                except:
                    pass
        else:
            await callback.answer("Товар уже выдан!", show_alert=True)
    else:
        await callback.answer("Оплата не найдена", show_alert=True)

# ==================== Profile ====================
@router.callback_query(F.data == "my_purchases")
async def cb_my_purchases(callback: types.CallbackQuery):
    purchases = await get_user_purchases(callback.from_user.id)

    if not purchases:
        await callback.answer("У вас пока нет покупок", show_alert=True)
        return

    text = "<b>Мои покупки</b>\n\n"
    for p in purchases:
        text += f"{p['product_name']} — {p['price_rub']:.0f}₽ ({p['purchased_at'][:10]})\n"

    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()

# ==================== Admin Handlers ====================
@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "<blockquote><b>Админ панель</b></blockquote>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    users, purchases, revenue, products = await get_stats()

    text = "<b>Статистика</b>\n\n"
    text += f"<b>Пользователей:</b> {users}\n"
    text += f"<b>Покупок:</b> {purchases}\n"
    text += f"<b>Выручка:</b> {revenue:.0f}₽\n"
    text += f"<b>Товаров:</b> {products}"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()

# ==================== Admin Media ====================
@router.callback_query(F.data == "admin_media")
async def cb_admin_media(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Главное меню",
            callback_data="setmedia_main_menu",
            icon_custom_emoji_id="5873147866364514353"
        )],
        [InlineKeyboardButton(
            text="Меню магазина",
            callback_data="setmedia_shop_menu",
            icon_custom_emoji_id="5884479287171485878"
        )],
        [InlineKeyboardButton(
            text="О шопе",
            callback_data="setmedia_about_menu",
            icon_custom_emoji_id="6028435952299413210"
        )],
        [InlineKeyboardButton(
            text="Поддержка",
            callback_data="setmedia_support_menu",
            icon_custom_emoji_id="6039422865189638057"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_panel"
        )]
    ])

    await callback.message.edit_text(
        "<b>Настройка медиа</b>\n\n<blockquote>Выберите раздел для установки медиа:</blockquote>",
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
        [InlineKeyboardButton(
            text="Удалить медиа",
            callback_data=f"delmedia_{media_key}",
            icon_custom_emoji_id="5870875489362513438"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_media"
        )]
    ])

    await callback.message.edit_text(
        "<b>Установка медиа</b>\n\n<blockquote>Отправьте фото, видео или GIF для этого раздела:</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data.startswith("delmedia_"))
async def cb_delmedia(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    media_key = callback.data.replace("delmedia_", "")
    async with get_db() as conn:
        await conn.execute('DELETE FROM media_settings WHERE key = $1', media_key)

    await state.clear()
    await callback.answer("Медиа удалено", show_alert=True)
    await cb_admin_media(callback)

@router.message(AdminStates.set_media_file,
                F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION]))
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
    await message.answer("<b>Медиа успешно установлено!</b>", parse_mode="HTML", reply_markup=admin_back())

# ==================== Admin Broadcast ====================
@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.broadcast_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_panel"
        )]
    ])

    await callback.message.edit_text(
        "<b>Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF для рассылки:</blockquote>",
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

    status_msg = await message.answer("Рассылка начата...")

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id,
                                     caption=message.caption, parse_mode="HTML")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id,
                                     caption=message.caption, parse_mode="HTML")
            elif message.animation:
                await bot.send_animation(user_id, message.animation.file_id,
                                         caption=message.caption, parse_mode="HTML")
            else:
                await bot.send_message(user_id, message.text, parse_mode="HTML")
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"<b>Рассылка завершена!</b>\n\n"
        f"Успешно: {success}\n"
        f"Ошибок: {failed}",
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
                callback_data=f"editcat_{cat['id']}",
                icon_custom_emoji_id="5870528606328852614"
            ),
            InlineKeyboardButton(
                text="",
                callback_data=f"delcat_{cat['id']}",
                icon_custom_emoji_id="5870875489362513438"
            )
        ])
    keyboard.append([InlineKeyboardButton(
        text="Добавить категорию",
        callback_data="addcat",
        icon_custom_emoji_id="5870633910337015697"
    )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_panel"
    )])

    await callback.message.edit_text(
        "<b>Категории</b>\n\n<blockquote>Управление категориями товаров:</blockquote>",
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
        "<b>Новая категория</b>\n\n<blockquote>Введите название категории:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Назад",
                callback_data="admin_categories"
            )]
        ])
    )
    await callback.answer()

@router.message(AdminStates.add_category_name)
async def process_category_name(message: types.Message, state: FSMContext):
    await add_category(message.text)
    await state.clear()
    await message.answer("<b>Категория добавлена!</b>", parse_mode="HTML", reply_markup=admin_back())

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
            icon_custom_emoji_id="5870528606328852614"
        )])
    keyboard.append([InlineKeyboardButton(
        text="Добавить товар",
        callback_data="addprod",
        icon_custom_emoji_id="5870633910337015697"
    )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_panel"
    )])

    await callback.message.edit_text(
        "<b>Товары</b>\n\n<blockquote>Выберите категорию для просмотра товаров:</blockquote>",
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
            InlineKeyboardButton(
                text=f"{prod['name']} — {prod['price_rub']:.0f}₽",
                callback_data=f"viewprod_{prod['id']}",
                icon_custom_emoji_id="5884479287171485878"
            ),
            InlineKeyboardButton(
                text="",
                callback_data=f"delprod_{prod['id']}",
                icon_custom_emoji_id="5870875489362513438"
            )
        ])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_products"
    )])

    await callback.message.edit_text(
        "<blockquote>Товары в категории:</blockquote>",
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
    await callback.answer("Товар удалён", show_alert=True)
    await cb_admin_products(callback)

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
            icon_custom_emoji_id="5870528606328852614"
        )])
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="admin_products"
    )])

    await callback.message.edit_text(
        "<b>Новый товар</b>\n\n<blockquote>Выберите категорию для товара:</blockquote>",
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
        "<b>Новый товар</b>\n\n<blockquote>Введите название товара:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Назад",
                callback_data="addprod"
            )]
        ])
    )
    await callback.answer()

@router.message(AdminStates.add_product_name)
async def process_product_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.add_product_desc)

    await message.answer(
        "<b>Новый товар</b>\n\n<blockquote>Введите описание товара:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Назад",
                callback_data="addprod"
            )]
        ])
    )

@router.message(AdminStates.add_product_desc)
async def process_product_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AdminStates.add_product_price)

    await message.answer(
        "<b>Новый товар</b>\n\n<blockquote>Введите цену в рублях:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Назад",
                callback_data="addprod"
            )]
        ])
    )

@router.message(AdminStates.add_product_price)
async def process_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        await state.update_data(price=price)
        await state.set_state(AdminStates.add_product_type)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Текстовый",
                callback_data="prodtype_text",
                icon_custom_emoji_id="5870801517140775623"
            )],
            [InlineKeyboardButton(
                text="Файловый",
                callback_data="prodtype_file",
                icon_custom_emoji_id="5870528606328852614"
            )],
            [InlineKeyboardButton(
                text="Назад",
                callback_data="addprod"
            )]
        ])

        await message.answer(
            "<b>Новый товар</b>\n\n<blockquote>Выберите тип товара:</blockquote>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except ValueError:
        await message.answer("Введите корректную цену (число)")

@router.callback_query(F.data.startswith("prodtype_"), AdminStates.add_product_type)
async def cb_prodtype(callback: types.CallbackQuery, state: FSMContext):
    prod_type = callback.data.split("_")[1]
    await state.update_data(product_type=prod_type)
    await state.update_data(files=[])

    if prod_type == "text":
        await state.set_state(AdminStates.add_product_content)
        await callback.message.edit_text(
            "<b>Новый товар</b>\n\n<blockquote>Введите текстовый контент товара:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="Назад",
                    callback_data="addprod"
                )]
            ])
        )
    else:
        await state.set_state(AdminStates.add_product_file)
        await callback.message.edit_text(
            "<b>Новый товар</b>\n\n<blockquote>Отправьте файлы товара (до 10 файлов). Отправьте /done когда закончите:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="Готово",
                    callback_data="files_done"
                )],
                [InlineKeyboardButton(
                    text="Назад",
                    callback_data="addprod"
                )]
            ])
        )
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
    await message.answer("<b>Товар успешно добавлен!</b>", parse_mode="HTML", reply_markup=admin_back())

@router.message(AdminStates.add_product_file, F.document)
async def process_product_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    files = data.get('files', [])
    
    if len(files) >= 10:
        await message.answer("Достигнут лимит в 10 файлов. Отправьте /done для завершения.")
        return
    
    files.append(message.document.file_id)
    await state.update_data(files=files)
    await message.answer(f"Файл добавлен ({len(files)}/10). Отправьте ещё или нажмите «Готово».")

@router.message(AdminStates.add_product_file, Command("done"))
@router.callback_query(F.data == "files_done", AdminStates.add_product_file)
async def process_files_done(event: types.Message | types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    files = data.get('files', [])
    
    if not files:
        if isinstance(event, types.CallbackQuery):
            await event.answer("Добавьте хотя бы один файл!", show_alert=True)
        else:
            await event.answer("Добавьте хотя бы один файл!")
        return
    
    await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        'file',
        files=files
    )
    await state.clear()
    
    text = "<b>Товар успешно добавлен!</b>"
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML")
        await event.message.answer("Вернуться в админ панель", reply_markup=admin_back())
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=admin_back())

# ==================== Admin Settings ====================
@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Изменить описание",
            callback_data="edit_shop_info",
            icon_custom_emoji_id="5870676941614354370"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_panel"
        )]
    ])

    await callback.message.edit_text(
        "<b>Настройки</b>",
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
        "<b>Описание магазина</b>\n\n<blockquote>Введите новое описание магазина:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Назад",
                callback_data="admin_settings"
            )]
        ])
    )
    await callback.answer()

@router.message(AdminStates.edit_shop_info)
async def process_shop_info(message: types.Message, state: FSMContext):
    await set_shop_setting("shop_info", message.text)
    await state.clear()
    await message.answer("<b>Описание магазина обновлено!</b>", parse_mode="HTML", reply_markup=admin_back())

# ==================== Cancel State Handler ====================
@router.callback_query(
    F.data.in_(["admin_panel", "admin_media", "admin_categories", "admin_products", "addprod", "admin_settings"]))
async def cancel_state(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()

# ==================== Main ====================
async def main():
    await create_db_pool()
    await init_db()
    logging.basicConfig(level=logging.INFO)
    print("\033[35m" + "═" * 40)
    print("  🤖 Создатель бота: t.me/fuck_zaza")
    print("═" * 40 + "\033[0m")
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
