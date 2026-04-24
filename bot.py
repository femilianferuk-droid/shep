import asyncio
import logging
import os
import uuid
import hashlib
import hmac
import asyncpg
import aiohttp
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
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ROLLYPAY_API_KEY = os.getenv("ROLLYPAY_API_KEY")
ROLLYPAY_SIGNING_SECRET = os.getenv("ROLLYPAY_SIGNING_SECRET")
ROLLYPAY_TERMINAL_ID = os.getenv("ROLLYPAY_TERMINAL_ID")

ADMIN_IDS = [7973988177]
SUPPORT_USERNAME = "@VestSupport"
SHOP_NAME = "Vest Creator"
USDT_RATE = 90.0

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

db_pool = None


class AdminStates(StatesGroup):
    broadcast_text = State()
    set_media_file = State()
    add_category_name = State()
    add_product_name = State()
    add_product_desc = State()
    add_product_price = State()
    add_product_type = State()
    add_product_content = State()
    add_product_file = State()
    edit_shop_info = State()
    edit_welcome_message = State()
    edit_help_message = State()
    edit_user_balance = State()


async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
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
                price REAL NOT NULL,
                product_type TEXT DEFAULT 'text',
                content TEXT,
                file_id TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT
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
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_id INTEGER,
                invoice_id TEXT,
                payment_id TEXT,
                amount REAL,
                status TEXT DEFAULT 'pending',
                payment_method TEXT DEFAULT 'cryptobot',
                created_at TEXT
            )
        ''')


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
        return await conn.fetch(
            'SELECT * FROM products WHERE category_id = $1 AND is_active = 1',
            category_id
        )


async def get_product(product_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)


async def add_product(category_id, name, description, price, product_type, content=None, file_id=None):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO products (category_id, name, description, price, product_type, content, file_id, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ''', category_id, name, description, price, product_type, content, file_id, datetime.now().isoformat())


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


async def save_payment(user_id: int, product_id: int, invoice_id: str, amount: float, payment_method: str = "cryptobot", payment_id: str = None):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO payments (user_id, product_id, invoice_id, payment_id, amount, status, payment_method, created_at)
            VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7)
        ''', user_id, product_id, invoice_id, payment_id, amount, payment_method, datetime.now().isoformat())


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


async def update_user_balance(user_id: int, amount: float):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET balance = balance + $1 WHERE user_id = $2', amount, user_id)


async def get_user_by_id(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)


async def create_cryptobot_invoice(amount: float, description: str, payload: str):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    
    usdt_amount = round(amount / USDT_RATE, 2)
    
    data = {
        "asset": "USDT",
        "amount": str(usdt_amount),
        "description": description,
        "payload": payload,
        "allow_comments": False,
        "allow_anonymous": False
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result["result"]
                else:
                    logging.error(f"CryptoBot error: {result}")
    except Exception as e:
        logging.error(f"CryptoBot exception: {e}")
    
    return None


async def check_cryptobot_invoice(invoice_id: str):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                result = await resp.json()
                if result.get("ok") and result["result"]["items"]:
                    return result["result"]["items"][0]
    except Exception as e:
        logging.error(f"CryptoBot check error: {e}")
    
    return None


async def create_rollypay_payment(amount: float, order_id: str, description: str):
    """Создание платежа через RollyPay API"""
    url = "https://rollypay.io/api/v1/payments"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": ROLLYPAY_API_KEY,
        "X-Nonce": str(uuid.uuid4())
    }
    
    data = {
        "amount": f"{amount:.2f}",
        "payment_currency": "RUB",
        "order_id": order_id,
        "description": description,
        "terminal_id": ROLLYPAY_TERMINAL_ID
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await resp.json()
                logging.info(f"RollyPay create payment response: {result}")
                return result
    except Exception as e:
        logging.error(f"RollyPay create payment exception: {e}")
    
    return None


async def check_rollypay_payment(payment_id: str):
    """Проверка статуса платежа через RollyPay API"""
    url = f"https://rollypay.io/api/v1/payments/{payment_id}"
    headers = {
        "X-API-Key": ROLLYPAY_API_KEY,
        "X-Nonce": str(uuid.uuid4())
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                result = await resp.json()
                logging.info(f"RollyPay check payment response: {result}")
                return result
    except Exception as e:
        logging.error(f"RollyPay check payment exception: {e}")
    
    return None


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Купить", icon_custom_emoji_id="5884479287171485878"),
                KeyboardButton(text="Помощь", icon_custom_emoji_id="6039486778597970865")
            ],
            [
                KeyboardButton(text="О шопе", icon_custom_emoji_id="6028435952299413210"),
                KeyboardButton(text="Поддержка", icon_custom_emoji_id="5870994129244131212")
            ]
        ],
        resize_keyboard=True
    )


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Статистика", callback_data="admin_stats", icon_custom_emoji_id="5870921681735781843")],
        [InlineKeyboardButton(text="Медиа", callback_data="admin_media", icon_custom_emoji_id="6035128606563241721")],
        [InlineKeyboardButton(text="Рассылка", callback_data="admin_broadcast", icon_custom_emoji_id="6039422865189638057")],
        [InlineKeyboardButton(text="Товары", callback_data="admin_products", icon_custom_emoji_id="5884479287171485878")],
        [InlineKeyboardButton(text="Категории", callback_data="admin_categories", icon_custom_emoji_id="5870528606328852614")],
        [InlineKeyboardButton(text="Пользователи", callback_data="admin_users", icon_custom_emoji_id="5870772616305839506")],
        [InlineKeyboardButton(text="Настройки", callback_data="admin_settings", icon_custom_emoji_id="5870982283724328568")]
    ])


def admin_back():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])


def back_button(callback_data: str = "main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])


async def send_with_media(chat_id: int, text: str, media_key: str, reply_markup=None):
    try:
        media = await get_media(media_key)
        if media:
            if media["media_type"] == "photo":
                await bot.send_photo(chat_id, media["file_id"], caption=text or "", parse_mode="HTML", reply_markup=reply_markup)
            elif media["media_type"] == "video":
                await bot.send_video(chat_id, media["file_id"], caption=text or "", parse_mode="HTML", reply_markup=reply_markup)
            elif media["media_type"] == "animation":
                await bot.send_animation(chat_id, media["file_id"], caption=text or "", parse_mode="HTML", reply_markup=reply_markup)
            else:
                await bot.send_message(chat_id, text or "", parse_mode="HTML", reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id, text or "", parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error in send_with_media: {e}")
        try:
            await bot.send_message(chat_id, text or "", parse_mode="HTML", reply_markup=reply_markup)
        except:
            await bot.send_message(chat_id, "❌ Ошибка при отправке сообщения")


async def set_commands(user_id: int):
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="🆘 Помощь")
    ]
    if user_id in ADMIN_IDS:
        commands.append(BotCommand(command="admin", description="Админ панель"))
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user)
    await set_commands(message.from_user.id)
    
    welcome_text = await get_shop_setting("welcome_message", f'<b><tg-emoji emoji-id="5873147866364514353">🏪</tg-emoji> {SHOP_NAME}</b>\n\n<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите действие:</blockquote>')
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = await get_shop_setting("help_message", f'<b><tg-emoji emoji-id="6039486778597970865">🆘</tg-emoji> Помощь</b>\n\n<blockquote>Добро пожаловать в {SHOP_NAME}!\n\n📦 <b>Как купить товар:</b>\n1. Нажмите кнопку "Купить"\n2. Выберите категорию\n3. Выберите товар\n4. Выберите способ оплаты\n\n💰 <b>Способы оплаты:</b>\n• Баланс (внутренний кошелёк)\n• CryptoBot (USDT)\n• Рубли (RollyPay)\n\n🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}</blockquote>')
    await send_with_media(message.chat.id, help_text, "help_menu", None)


@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await message.answer(
        '<blockquote><tg-emoji emoji-id="6030400221232501136">🎩</tg-emoji> <b>Админ панель</b></blockquote>',
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data == "main")
async def cb_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    welcome_text = await get_shop_setting("welcome_message", f'<b><tg-emoji emoji-id="5873147866364514353">🏪</tg-emoji> {SHOP_NAME}</b>\n\n<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите действие:</blockquote>')
    try:
        await callback.message.delete()
    except:
        pass
    await bot.send_message(callback.from_user.id, welcome_text, parse_mode="HTML", reply_markup=main_keyboard())
    await callback.answer()


@router.message(F.text == "Купить")
async def text_shop(message: types.Message):
    categories = await get_categories()
    if not categories:
        await message.answer("<tg-emoji emoji-id=\"5870657884844462243\">❌</tg-emoji> Категории пока не добавлены", parse_mode="HTML")
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=cat['name'], callback_data=f"cat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main")])

    text = '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Каталог товаров</b>\n\n<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите категорию:</blockquote>'
    await send_with_media(message.chat.id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.message(F.text == "Помощь")
async def text_help(message: types.Message):
    help_text = await get_shop_setting("help_message", f'<b><tg-emoji emoji-id="6039486778597970865">🆘</tg-emoji> Помощь</b>\n\n<blockquote>Добро пожаловать в {SHOP_NAME}!\n\n📦 <b>Как купить товар:</b>\n1. Нажмите кнопку "Купить"\n2. Выберите категорию\n3. Выберите товар\n4. Выберите способ оплаты\n\n💰 <b>Способы оплаты:</b>\n• Баланс\n• CryptoBot (USDT)\n• Рубли (RollyPay)\n\n🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}</blockquote>')
    await send_with_media(message.chat.id, help_text, "help_menu", None)


@router.message(F.text == "О шопе")
async def text_about(message: types.Message):
    info = await get_shop_setting("shop_info", "Информация о магазине не заполнена.")
    text = f'<b><tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> О шопе</b>\n\n<blockquote>{info}</blockquote>'
    await send_with_media(message.chat.id, text, "about_menu", None)


@router.message(F.text == "Поддержка")
async def text_support(message: types.Message):
    text = f'<b><tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Поддержка</b>\n\n<blockquote>По всем вопросам обращайтесь: {SUPPORT_USERNAME}</blockquote>'
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Написать", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}", icon_custom_emoji_id="5870676941614354370")]
    ])
    await send_with_media(message.chat.id, text, "support_menu", keyboard)


@router.callback_query(F.data == "shop")
async def cb_shop(callback: types.CallbackQuery):
    categories = await get_categories()
    if not categories:
        await callback.answer("Категории пока не добавлены", show_alert=True)
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=cat['name'], callback_data=f"cat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main")])

    text = '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Каталог товаров</b>\n\n<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите категорию:</blockquote>'
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except:
        await callback.message.delete()
        await bot.send_message(callback.from_user.id, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
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
            callback_data=f"prod_{prod['id']}"
        )])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="shop")])

    try:
        await callback.message.edit_text(
            '<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите товар:</blockquote>',
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    except Exception as e:
        logging.error(f"Error editing message: {e}")
        await callback.message.delete()
        await bot.send_message(
            callback.from_user.id,
            '<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите товар:</blockquote>',
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("prod_"))
async def cb_product(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        prod_id = int(parts[1])
    except:
        await callback.answer("Ошибка данных товара", show_alert=True)
        return
    
    product = await get_product(prod_id)
    user = await get_user(callback.from_user.id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> {product["name"]}</b>\n\n'
    text += f'<blockquote>{product["description"]}</blockquote>\n\n'
    text += f'<b><tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Цена:</b> {product["price"]:.0f}₽\n'
    text += f'<b><tg-emoji emoji-id="5769126056262898415">💰</tg-emoji> Ваш баланс:</b> {user["balance"]:.0f}₽'

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить за баланс", callback_data=f"buybal_{prod_id}")],
        [InlineKeyboardButton(text="Оплатить CryptoBot (USDT)", callback_data=f"buycr_{prod_id}")],
        [InlineKeyboardButton(text="Оплатить Рубли (RollyPay)", callback_data=f"buyrb_{prod_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"cat_{product['category_id']}")]
    ])

    try:
        await callback.message.delete()
    except:
        pass
    await send_with_media(callback.from_user.id, text, f"product_{prod_id}", keyboard)
    await callback.answer()


# Покупка за баланс
@router.callback_query(F.data.startswith("buybal_"))
async def cb_buy_balance(callback: types.CallbackQuery):
    try:
        prod_id = int(callback.data.split("_")[1])
    except:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    
    product = await get_product(prod_id)
    user = await get_user(callback.from_user.id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    if user["balance"] < product["price"]:
        text = f'<b><tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Недостаточно средств!</b>\n\n<tg-emoji emoji-id="5769126056262898415">💰</tg-emoji> Ваш баланс: {user["balance"]:.0f}₽\n<tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Цена товара: {product["price"]:.0f}₽'
        await send_with_media(callback.from_user.id, text, "payment_failed", back_button("shop"))
        await callback.answer()
        return

    await update_user_balance(callback.from_user.id, -product["price"])
    await add_purchase(callback.from_user.id, prod_id, product["price"])

    text = f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Покупка успешна!</b>\n\n<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Товар:</b> {product["name"]}\n\n'

    if product['product_type'] == 'text':
        text += f'<blockquote>{product["content"]}</blockquote>'
        await send_with_media(callback.from_user.id, text, "payment_success", back_button("shop"))
    else:
        await send_with_media(callback.from_user.id, text, "payment_success", None)
        await bot.send_document(callback.from_user.id, product['file_id'])
        await bot.send_message(
            callback.from_user.id,
            "<tg-emoji emoji-id=\"6039451237743595514\">📎</tg-emoji> Ваш товар",
            parse_mode="HTML",
            reply_markup=back_button("shop")
        )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id,
                                   f'<b><tg-emoji emoji-id="5904462880941545555">💰</tg-emoji> Новая покупка (баланс)!</b>\n\n'
                                   f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Покупатель: @{callback.from_user.username or "Без юзернейма"}\n'
                                   f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар: {product["name"]}\n'
                                   f'<tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма: {product["price"]:.0f}₽',
                                   parse_mode="HTML"
                                   )
        except:
            pass
    
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()


# Покупка через CryptoBot
@router.callback_query(F.data.startswith("buycr_"))
async def cb_buy_crypto(callback: types.CallbackQuery):
    try:
        prod_id = int(callback.data.split("_")[1])
    except:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    invoice = await create_cryptobot_invoice(
        amount=product['price'],
        description=f"Покупка: {product['name']}",
        payload=f"{callback.from_user.id}:{prod_id}"
    )

    if not invoice:
        await callback.answer("Ошибка создания платежа. Проверьте токен CryptoBot!", show_alert=True)
        return

    await save_payment(callback.from_user.id, prod_id, str(invoice['invoice_id']), product['price'], "cryptobot")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=invoice['pay_url'])],
        [InlineKeyboardButton(text="Проверить оплату", callback_data=f"chkcr_{invoice['invoice_id']}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"prod_{prod_id}")]
    ])

    usdt_amount = product['price'] / USDT_RATE
    text = f'<b><tg-emoji emoji-id="5260752406890711732">👾</tg-emoji> Оплата через CryptoBot</b>\n\n'
    text += f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар:</b> {product["name"]}\n'
    text += f'<b><tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма:</b> {product["price"]:.0f}₽ ({usdt_amount:.2f} USDT)\n\n'
    text += '<blockquote>Нажмите «Оплатить» и после оплаты нажмите «Проверить оплату»</blockquote>'

    await send_with_media(callback.from_user.id, text, "payment_page", keyboard)
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()


# Проверка оплаты CryptoBot
@router.callback_query(F.data.startswith("chkcr_"))
async def cb_check_crypto(callback: types.CallbackQuery):
    try:
        invoice_id = callback.data.split("_")[1]
    except:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    
    invoice = await check_cryptobot_invoice(invoice_id)

    if not invoice:
        await callback.answer("Ошибка проверки платежа", show_alert=True)
        return

    if invoice['status'] == 'paid':
        payment = await get_payment(invoice_id)
        if payment and payment['status'] == 'pending':
            await update_payment_status(invoice_id, 'paid')
            product = await get_product(payment['product_id'])
            await add_purchase(callback.from_user.id, payment['product_id'], payment['amount'])

            text = f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Оплата успешна!</b>\n\n<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Товар:</b> {product["name"]}\n\n'

            if product['product_type'] == 'text':
                text += f'<blockquote>{product["content"]}</blockquote>'
                await send_with_media(callback.from_user.id, text, "payment_success", back_button("shop"))
            else:
                await send_with_media(callback.from_user.id, text, "payment_success", None)
                await bot.send_document(callback.from_user.id, product['file_id'])
                await bot.send_message(
                    callback.from_user.id,
                    "<tg-emoji emoji-id=\"6039451237743595514\">📎</tg-emoji> Ваш товар",
                    parse_mode="HTML",
                    reply_markup=back_button("shop")
                )

            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id,
                                           f'<b><tg-emoji emoji-id="5260752406890711732">👾</tg-emoji> Новая покупка (CryptoBot)!</b>\n\n'
                                           f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Покупатель: @{callback.from_user.username or "Без юзернейма"}\n'
                                           f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар: {product["name"]}\n'
                                           f'<tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма: {payment["amount"]:.0f}₽',
                                           parse_mode="HTML"
                                           )
                except:
                    pass
            
            try:
                await callback.message.delete()
            except:
                pass
        else:
            await callback.answer("Товар уже выдан!", show_alert=True)
    else:
        await callback.answer("Оплата не найдена. Попробуйте позже.", show_alert=True)


# Покупка через RollyPay (Рубли)
@router.callback_query(F.data.startswith("buyrb_"))
async def cb_buy_rub(callback: types.CallbackQuery):
    try:
        prod_id = int(callback.data.split("_")[1])
    except:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    order_id = f"order_{callback.from_user.id}_{prod_id}_{int(datetime.now().timestamp())}"
    
    result = await create_rollypay_payment(
        amount=product['price'],
        order_id=order_id,
        description=f"Покупка: {product['name']}"
    )

    if not result or not result.get("pay_url"):
        await callback.answer("Ошибка создания платежа RollyPay!", show_alert=True)
        return

    payment_id = result.get("payment_id", "")
    await save_payment(callback.from_user.id, prod_id, order_id, product['price'], "rollypay", payment_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить RollyPay", url=result['pay_url'])],
        [InlineKeyboardButton(text="Проверить оплату", callback_data=f"chkrb_{order_id}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"prod_{prod_id}")]
    ])

    text = f'<b><tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Оплата через RollyPay (Рубли)</b>\n\n'
    text += f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар:</b> {product["name"]}\n'
    text += f'<b><tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма:</b> {product["price"]:.0f}₽\n\n'
    text += '<blockquote>Нажмите «Оплатить» и после оплаты нажмите «Проверить оплату»</blockquote>'

    await send_with_media(callback.from_user.id, text, "payment_page", keyboard)
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()


# Проверка оплаты RollyPay
@router.callback_query(F.data.startswith("chkrb_"))
async def cb_check_rub(callback: types.CallbackQuery):
    try:
        order_id = callback.data.split("_", 1)[1] if "_" in callback.data else callback.data
    except:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    
    payment = await get_payment(order_id)

    if not payment or not payment['payment_id']:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    result = await check_rollypay_payment(payment['payment_id'])

    if not result:
        await callback.answer("Ошибка проверки платежа", show_alert=True)
        return

    if result.get('status') == 'paid':
        if payment['status'] == 'pending':
            await update_payment_status(order_id, 'paid')
            product = await get_product(payment['product_id'])
            await add_purchase(callback.from_user.id, payment['product_id'], payment['amount'])

            text = f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Оплата успешна!</b>\n\n<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Товар:</b> {product["name"]}\n\n'

            if product['product_type'] == 'text':
                text += f'<blockquote>{product["content"]}</blockquote>'
                await send_with_media(callback.from_user.id, text, "payment_success", back_button("shop"))
            else:
                await send_with_media(callback.from_user.id, text, "payment_success", None)
                await bot.send_document(callback.from_user.id, product['file_id'])
                await bot.send_message(
                    callback.from_user.id,
                    "<tg-emoji emoji-id=\"6039451237743595514\">📎</tg-emoji> Ваш товар",
                    parse_mode="HTML",
                    reply_markup=back_button("shop")
                )

            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id,
                                           f'<b><tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Новая покупка (RollyPay)!</b>\n\n'
                                           f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Покупатель: @{callback.from_user.username or "Без юзернейма"}\n'
                                           f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар: {product["name"]}\n'
                                           f'<tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма: {payment["amount"]:.0f}₽',
                                           parse_mode="HTML"
                                           )
                except:
                    pass
            
            try:
                await callback.message.delete()
            except:
                pass
        else:
            await callback.answer("Товар уже выдан!", show_alert=True)
    else:
        await callback.answer("Оплата не найдена. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        '<blockquote><tg-emoji emoji-id="6030400221232501136">🎩</tg-emoji> <b>Админ панель</b></blockquote>',
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    users, purchases, revenue, products = await get_stats()

    text = f'<b><tg-emoji emoji-id="5870921681735781843">📊</tg-emoji> Статистика</b>\n\n'
    text += f'<tg-emoji emoji-id="5870772616305839506">👥</tg-emoji> <b>Пользователей:</b> {users}\n'
    text += f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Покупок:</b> {purchases}\n'
    text += f'<tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> <b>Выручка:</b> {revenue:.0f}₽\n'
    text += f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Товаров:</b> {products}'

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить баланс пользователя", callback_data="edit_user_balance", icon_custom_emoji_id="5769126056262898415")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5870772616305839506">👥</tg-emoji> Управление пользователями</b>',
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data == "edit_user_balance")
async def cb_edit_user_balance(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.edit_user_balance)

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5769126056262898415">💰</tg-emoji> Изменить баланс</b>\n\n<blockquote>Введите ID пользователя и сумму через пробел\nПример: <code>123456789 100</code> (для добавления)\n<code>123456789 -50</code> (для списания)</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_users")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.edit_user_balance)
async def process_edit_balance(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        amount = float(parts[1])

        user = await get_user_by_id(user_id)
        if not user:
            await message.answer(
                '<b><tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Пользователь не найден!</b>',
                parse_mode="HTML",
                reply_markup=admin_back()
            )
            await state.clear()
            return

        await update_user_balance(user_id, amount)
        new_balance = user["balance"] + amount

        await message.answer(
            f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Баланс обновлен!</b>\n\n'
            f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> ID: <code>{user_id}</code>\n'
            f'<tg-emoji emoji-id="5769126056262898415">💰</tg-emoji> Новый баланс: {new_balance:.0f}₽',
            parse_mode="HTML",
            reply_markup=admin_back()
        )

        try:
            await bot.send_message(
                user_id,
                f'<b><tg-emoji emoji-id="5769126056262898415">💰</tg-emoji> Ваш баланс изменен!</b>\n\n'
                f'Сумма: {"+" if amount > 0 else ""}{amount:.0f}₽\n'
                f'Текущий баланс: {new_balance:.0f}₽',
                parse_mode="HTML"
            )
        except:
            pass

        await state.clear()

    except (ValueError, IndexError):
        await message.answer(
            '<b><tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Неверный формат!</b>\nИспользуйте: <code>ID СУММА</code>',
            parse_mode="HTML"
        )


@router.callback_query(F.data == "admin_media")
async def cb_admin_media(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Главное меню", callback_data="setmedia_main_menu", icon_custom_emoji_id="5873147866364514353")],
        [InlineKeyboardButton(text="Меню магазина", callback_data="setmedia_shop_menu", icon_custom_emoji_id="5884479287171485878")],
        [InlineKeyboardButton(text="О шопе", callback_data="setmedia_about_menu", icon_custom_emoji_id="6028435952299413210")],
        [InlineKeyboardButton(text="Помощь", callback_data="setmedia_help_menu", icon_custom_emoji_id="6039486778597970865")],
        [InlineKeyboardButton(text="Поддержка", callback_data="setmedia_support_menu", icon_custom_emoji_id="5870994129244131212")],
        [InlineKeyboardButton(text="Страница оплаты", callback_data="setmedia_payment_page", icon_custom_emoji_id="5769126056262898415")],
        [InlineKeyboardButton(text="Успешная оплата", callback_data="setmedia_payment_success", icon_custom_emoji_id="5870633910337015697")],
        [InlineKeyboardButton(text="Неуспешная оплата", callback_data="setmedia_payment_failed", icon_custom_emoji_id="5870657884844462243")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="6035128606563241721">🖼</tg-emoji> Настройка медиа</b>\n\n<blockquote>Выберите раздел для установки медиа:</blockquote>',
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
        [InlineKeyboardButton(text="Удалить медиа", callback_data=f"delmedia_{media_key}", icon_custom_emoji_id="5870875489362513438")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_media")]
    ])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="6035128606563241721">🖼</tg-emoji> Установка медиа</b>\n\n<blockquote>Отправьте фото, видео или GIF для этого раздела:</blockquote>',
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
        await message.answer("Неподдерживаемый формат", parse_mode="HTML", reply_markup=admin_back())
        return

    await set_media(media_key, media_type, file_id)
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Медиа успешно установлено!</b>',
        parse_mode="HTML",
        reply_markup=admin_back()
    )


@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.broadcast_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="6039422865189638057">📣</tg-emoji> Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF для рассылки:</blockquote>',
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

    status_msg = await message.answer(
        '<b><tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> Рассылка начата...</b>',
        parse_mode="HTML"
    )

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.animation:
                await bot.send_animation(user_id, message.animation.file_id, caption=message.caption or "", parse_mode="HTML")
            else:
                await bot.send_message(user_id, message.html_text, parse_mode="HTML")
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Рассылка завершена!</b>\n\n'
        f'<tg-emoji emoji-id="5963103826075456248">⬆</tg-emoji> Успешно: {success}\n'
        f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Ошибок: {failed}',
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
            InlineKeyboardButton(text=cat['name'], callback_data=f"editcat_{cat['id']}"),
            InlineKeyboardButton(text="Удалить", callback_data=f"delcat_{cat['id']}", icon_custom_emoji_id="5870875489362513438")
        ])
    keyboard.append([InlineKeyboardButton(text="Добавить категорию", callback_data="addcat", icon_custom_emoji_id="5870633910337015697")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Категории</b>\n\n<blockquote>Управление категориями товаров:</blockquote>',
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
        '<b><tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Новая категория</b>\n\n<blockquote>Введите название категории:</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_categories")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.add_category_name)
async def process_category_name(message: types.Message, state: FSMContext):
    await add_category(message.text)
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Категория добавлена!</b>',
        parse_mode="HTML",
        reply_markup=admin_back()
    )


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
        keyboard.append([InlineKeyboardButton(text=cat['name'], callback_data=f"admincat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="Добавить товар", callback_data="addprod", icon_custom_emoji_id="5870633910337015697")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товары</b>\n\n<blockquote>Выберите категорию для просмотра товаров:</blockquote>',
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
            InlineKeyboardButton(text=f"{prod['name']} — {prod['price']:.0f}₽", callback_data=f"viewprod_{prod['id']}"),
            InlineKeyboardButton(text="Удалить", callback_data=f"delprod_{prod['id']}", icon_custom_emoji_id="5870875489362513438")
        ])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_products")])

    await callback.message.edit_text(
        '<blockquote><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товары в категории:</blockquote>',
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
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_products")]
    ])
    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Товар удален</b>',
        parse_mode="HTML",
        reply_markup=keyboard
    )


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
        keyboard.append([InlineKeyboardButton(text=cat['name'], callback_data=f"newprodcat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_products")])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Выберите категорию для товара:</blockquote>',
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
        '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите название товара:</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.add_product_name)
async def process_product_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.add_product_desc)

    await message.answer(
        '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите описание товара:</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])
    )


@router.message(AdminStates.add_product_desc)
async def process_product_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.html_text)
    await state.set_state(AdminStates.add_product_price)

    await message.answer(
        '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите цену в рублях:</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])
    )


@router.message(AdminStates.add_product_price)
async def process_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        await state.update_data(price=price)
        await state.set_state(AdminStates.add_product_type)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Текстовый", callback_data="prodtype_text")],
            [InlineKeyboardButton(text="Файловый", callback_data="prodtype_file")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])

        await message.answer(
            '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Выберите тип товара:</blockquote>',
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except ValueError:
        await message.answer("Введите корректную цену (число)", parse_mode="HTML")


@router.callback_query(F.data.startswith("prodtype_"))
async def cb_prodtype(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    prod_type = callback.data.split("_")[1]
    await state.update_data(product_type=prod_type)

    if prod_type == "text":
        await state.set_state(AdminStates.add_product_content)
        await callback.message.edit_text(
            '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Введите текстовый контент товара (данные, ключи, инструкции и т.д.):</blockquote>',
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
            ])
        )
    else:
        await state.set_state(AdminStates.add_product_file)
        await callback.message.edit_text(
            '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Отправьте файл товара:</blockquote>',
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
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
        content=message.html_text
    )
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Товар успешно добавлен!</b>',
        parse_mode="HTML",
        reply_markup=admin_back()
    )


@router.message(AdminStates.add_product_file, F.document)
async def process_product_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        'file',
        file_id=message.document.file_id
    )
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Товар успешно добавлен!</b>',
        parse_mode="HTML",
        reply_markup=admin_back()
    )


@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить описание магазина", callback_data="edit_shop_info", icon_custom_emoji_id="5870676941614354370")],
        [InlineKeyboardButton(text="Изменить приветствие", callback_data="edit_welcome_message", icon_custom_emoji_id="5870764288364252592")],
        [InlineKeyboardButton(text="Изменить помощь", callback_data="edit_help_message", icon_custom_emoji_id="6039486778597970865")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5870982283724328568">⚙</tg-emoji> Настройки</b>',
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
        '<b><tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> Описание магазина</b>\n\n<blockquote>Введите новое описание магазина:</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.edit_shop_info)
async def process_shop_info(message: types.Message, state: FSMContext):
    await set_shop_setting("shop_info", message.html_text)
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Описание магазина обновлено!</b>',
        parse_mode="HTML",
        reply_markup=admin_back()
    )


@router.callback_query(F.data == "edit_welcome_message")
async def cb_edit_welcome_message(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.edit_welcome_message)

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5870764288364252592">🙂</tg-emoji> Приветственное сообщение</b>\n\n<blockquote>Отправьте текст для приветственного сообщения (можно использовать HTML-форматирование и премиум эмодзи):</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.edit_welcome_message)
async def process_welcome_message(message: types.Message, state: FSMContext):
    await set_shop_setting("welcome_message", message.html_text)
    await state.clear()
    
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Приветственное сообщение обновлено!</b>\n\n<blockquote>Вот как оно выглядит:</blockquote>',
        parse_mode="HTML"
    )
    await message.answer(message.html_text, parse_mode="HTML", reply_markup=admin_back())


@router.callback_query(F.data == "edit_help_message")
async def cb_edit_help_message(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.edit_help_message)

    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="6039486778597970865">🆘</tg-emoji> Сообщение помощи</b>\n\n<blockquote>Отправьте текст для раздела "Помощь" (можно использовать HTML-форматирование и премиум эмодзи):</blockquote>',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.edit_help_message)
async def process_help_message(message: types.Message, state: FSMContext):
    await set_shop_setting("help_message", message.html_text)
    await state.clear()
    
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Сообщение помощи обновлено!</b>\n\n<blockquote>Вот как оно выглядит:</blockquote>',
        parse_mode="HTML"
    )
    await message.answer(message.html_text, parse_mode="HTML", reply_markup=admin_back())


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
