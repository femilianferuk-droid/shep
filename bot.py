import asyncio
import logging
import os
import asyncpg
import aiohttp
import json
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

ADMIN_IDS = [7973988177]
SUPPORT_USERNAME = "@VestSupport"
SHOP_NAME = "Vest Creator"
SBP_PHONE = "+79818376180"
SBP_BANK = "ЮМАНИ"
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
    add_product_files = State()
    edit_shop_info = State()
    edit_user_balance = State()


class UserStates(StatesGroup):
    waiting_sbp_screenshot = State()


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
                file_ids TEXT,
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
                amount REAL,
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
                status TEXT DEFAULT 'pending',
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


async def add_product(category_id, name, description, price, product_type, content=None, file_ids=None):
    async with db_pool.acquire() as conn:
        file_ids_json = json.dumps(file_ids) if file_ids else None
        await conn.execute('''
            INSERT INTO products (category_id, name, description, price, product_type, content, file_ids, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ''', category_id, name, description, price, product_type, content, file_ids_json, datetime.now().isoformat())


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


async def save_payment(user_id: int, product_id: int, invoice_id: str, amount: float):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO payments (user_id, product_id, invoice_id, amount, created_at)
            VALUES ($1, $2, $3, $4, $5)
        ''', user_id, product_id, invoice_id, amount, datetime.now().isoformat())


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


async def save_sbp_payment(user_id: int, product_id: int, amount: float):
    async with db_pool.acquire() as conn:
        return await conn.fetchval('''
            INSERT INTO sbp_payments (user_id, product_id, amount, created_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id
        ''', user_id, product_id, amount, datetime.now().isoformat())


async def create_invoice(amount: float, description: str, payload: str):
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


async def check_invoice(invoice_id: str):
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


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Купить", icon_custom_emoji_id="5884479287171485878"),
                KeyboardButton(text="Профиль", icon_custom_emoji_id="5870994129244131212")
            ],
            [
                KeyboardButton(text="О шопе", icon_custom_emoji_id="6028435952299413210"),
                KeyboardButton(text="Поддержка", icon_custom_emoji_id="6039486778597970865")
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
    media = await get_media(media_key)
    if media:
        if media["media_type"] == "photo":
            await bot.send_photo(chat_id, media["file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media["media_type"] == "video":
            await bot.send_video(chat_id, media["file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media["media_type"] == "animation":
            await bot.send_animation(chat_id, media["file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)


async def set_commands(user_id: int):
    commands = [BotCommand(command="start", description="Старт")]
    if user_id in ADMIN_IDS:
        commands.append(BotCommand(command="admin", description="Админ панель"))
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user)
    await set_commands(message.from_user.id)
    text = f'<b><tg-emoji emoji-id="5873147866364514353">🏪</tg-emoji> {SHOP_NAME}</b>\n\n<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите действие:</blockquote>'
    await message.answer(text, parse_mode="HTML", reply_markup=main_keyboard())


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
    text = f'<b><tg-emoji emoji-id="5873147866364514353">🏪</tg-emoji> {SHOP_NAME}</b>\n\n<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите действие:</blockquote>'
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
        await message.answer("<tg-emoji emoji-id=\"5870657884844462243\">❌</tg-emoji> Категории пока не добавлены", parse_mode="HTML")
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=cat['name'], callback_data=f"cat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main")])

    text = '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Каталог товаров</b>\n\n<blockquote><tg-emoji emoji-id="5893057118545646106">👇</tg-emoji> Выберите категорию:</blockquote>'
    await send_with_media(message.chat.id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.message(F.text == "Профиль")
async def text_profile(message: types.Message):
    user = await get_user(message.from_user.id)
    purchases = await get_user_purchases(message.from_user.id)

    text = f'<b><tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Мой профиль</b>\n\n'
    text += f'<tg-emoji emoji-id="5870772616305839506">🆔</tg-emoji> <b>ID:</b> <code>{message.from_user.id}</code>\n'
    text += f'<tg-emoji emoji-id="5769126056262898415">💰</tg-emoji> <b>Баланс:</b> {user["balance"]:.0f}₽\n'
    text += f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Покупок:</b> {user["total_purchases"]}\n'
    text += f'<tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> <b>Потрачено:</b> {user["total_spent"]:.0f}₽\n'
    text += f'<tg-emoji emoji-id="5890937706803894250">📅</tg-emoji> <b>Регистрация:</b> {user["registered_at"][:10]}\n'

    if purchases:
        text += f'\n<b><tg-emoji emoji-id="5870528606328852614">📋</tg-emoji> Последние покупки:</b>\n'
        for p in purchases[:5]:
            text += f'<tg-emoji emoji-id="5884479287171485878">•</tg-emoji> {p["product_name"]} — {p["price"]:.0f}₽\n'

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мои покупки", callback_data="my_purchases")]
    ])

    await send_with_media(message.chat.id, text, "profile_menu", keyboard)


@router.message(F.text == "О шопе")
async def text_about(message: types.Message):
    info = await get_shop_setting("shop_info", "Информация о магазине не заполнена.")
    text = f'<b><tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> О шопе</b>\n\n<blockquote>{info}</blockquote>'
    await send_with_media(message.chat.id, text, "about_menu", None)


@router.message(F.text == "Поддержка")
async def text_support(message: types.Message):
    text = f'<b><tg-emoji emoji-id="6039486778597970865">🛟</tg-emoji> Поддержка</b>\n\n<blockquote>По всем вопросам обращайтесь: {SUPPORT_USERNAME}</blockquote>'
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
    
    # Проверяем, можно ли редактировать сообщение
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
    prod_id = int(callback.data.split("_")[1])
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
        [InlineKeyboardButton(text="Купить за баланс", callback_data=f"buyball_{prod_id}", icon_custom_emoji_id="5769126056262898415")],
        [InlineKeyboardButton(text="Оплатить через СБП", callback_data=f"sbp_{prod_id}", icon_custom_emoji_id="5879814368572478751")],
        [InlineKeyboardButton(text="Оплатить CryptoBot", callback_data=f"buy_{prod_id}", icon_custom_emoji_id="5260752406890711732")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"cat_{product['category_id']}")]
    ])

    try:
        await callback.message.delete()
    except:
        pass
    await send_with_media(callback.from_user.id, text, f"product_{prod_id}", keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("buyball_"))
async def cb_buy_balance(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)
    user = await get_user(callback.from_user.id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    if user["balance"] < product["price"]:
        await callback.answer(f"Недостаточно средств! Ваш баланс: {user['balance']:.0f}₽", show_alert=True)
        return

    await update_user_balance(callback.from_user.id, -product["price"])
    await add_purchase(callback.from_user.id, prod_id, product["price"])

    text = f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Покупка успешна!</b>\n\n<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Товар:</b> {product["name"]}\n\n'

    if product['product_type'] == 'text':
        text += f'<blockquote>{product["content"]}</blockquote>'
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button("shop"))
    else:
        await callback.message.edit_text(text, parse_mode="HTML")
        file_ids = json.loads(product['file_ids']) if product['file_ids'] else []
        if file_ids:
            media_group = [types.InputMediaDocument(media=file_id) for file_id in file_ids[:10]]
            if media_group:
                await bot.send_media_group(callback.from_user.id, media_group)
        await bot.send_message(
            callback.from_user.id,
            "<tg-emoji emoji-id=\"6039451237743595514\">📎</tg-emoji> Ваши файлы",
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


@router.callback_query(F.data.startswith("sbp_"))
async def cb_sbp(callback: types.CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    await state.update_data(sbp_product_id=prod_id, sbp_amount=product["price"])
    await state.set_state(UserStates.waiting_sbp_screenshot)

    text = f'<b><tg-emoji emoji-id="5769126056262898415">🏧</tg-emoji> Оплата через СБП</b>\n\n'
    text += f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар:</b> {product["name"]}\n'
    text += f'<b><tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма к оплате:</b> {product["price"]:.0f}₽\n\n'
    text += f'<b><tg-emoji emoji-id="5870994129244131212">📱</tg-emoji> Номер для перевода:</b> <code>{SBP_PHONE}</code>\n'
    text += f'<b><tg-emoji emoji-id="5873147866364514353">🏦</tg-emoji> Банк:</b> {SBP_BANK}\n\n'
    text += f'<blockquote>После оплаты отправьте скриншот чека в этот чат и ожидайте подтверждения от поддержки {SUPPORT_USERNAME}</blockquote>'

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"prod_{prod_id}")]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.message(UserStates.waiting_sbp_screenshot, F.photo)
async def process_sbp_screenshot(message: types.Message, state: FSMContext):
    data = await state.get_data()
    prod_id = data.get("sbp_product_id")
    amount = data.get("sbp_amount")
    product = await get_product(prod_id)
    
    await state.clear()
    
    await save_sbp_payment(message.from_user.id, prod_id, amount)
    
    text = f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Чек получен!</b>\n\n'
    text += f'<blockquote>Ожидайте подтверждения оплаты от поддержки {SUPPORT_USERNAME}</blockquote>'
    
    await message.answer(text, parse_mode="HTML", reply_markup=back_button("shop"))
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id,
                message.photo[-1].file_id,
                caption=f'<b><tg-emoji emoji-id="5769126056262898415">🏧</tg-emoji> Новая оплата СБП!</b>\n\n'
                        f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Покупатель: @{message.from_user.username or "Без юзернейма"} (ID: {message.from_user.id})\n'
                        f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар: {product["name"]}\n'
                        f'<tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма: {amount:.0f}₽\n\n'
                        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Ожидает выдачи!',
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Failed to send to admin {admin_id}: {e}")


@router.callback_query(F.data.startswith("buy_"))
async def cb_buy(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    invoice = await create_invoice(
        amount=product['price'],
        description=f"Покупка: {product['name']}",
        payload=f"{callback.from_user.id}:{prod_id}"
    )

    if not invoice:
        await callback.answer("Ошибка создания платежа. Проверьте токен CryptoBot!", show_alert=True)
        return

    await save_payment(callback.from_user.id, prod_id, str(invoice['invoice_id']), product['price'])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=invoice['pay_url'], icon_custom_emoji_id="5260752406890711732")],
        [InlineKeyboardButton(text="Проверить оплату", callback_data=f"check_{invoice['invoice_id']}", icon_custom_emoji_id="5345906554510012647")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"prod_{prod_id}")]
    ])

    usdt_amount = product['price'] / USDT_RATE
    text = f'<b><tg-emoji emoji-id="5260752406890711732">👾</tg-emoji> Оплата через CryptoBot</b>\n\n'
    text += f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар:</b> {product["name"]}\n'
    text += f'<b><tg-emoji emoji-id="5904462880941545555">💵</tg-emoji> Сумма:</b> {product["price"]:.0f}₽ ({usdt_amount:.2f} USDT)\n\n'
    text += '<blockquote>Нажмите «Оплатить» и после оплаты нажмите «Проверить оплату»</blockquote>'

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

            text = f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Оплата успешна!</b>\n\n<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Товар:</b> {product["name"]}\n\n'

            if product['product_type'] == 'text':
                text += f'<blockquote>{product["content"]}</blockquote>'
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button("shop"))
            else:
                await callback.message.edit_text(text, parse_mode="HTML")
                file_ids = json.loads(product['file_ids']) if product['file_ids'] else []
                if file_ids:
                    media_group = [types.InputMediaDocument(media=file_id) for file_id in file_ids[:10]]
                    if media_group:
                        await bot.send_media_group(callback.from_user.id, media_group)
                await bot.send_message(
                    callback.from_user.id,
                    "<tg-emoji emoji-id=\"6039451237743595514\">📎</tg-emoji> Ваши файлы",
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
        else:
            await callback.answer("Товар уже выдан!", show_alert=True)
    else:
        await callback.answer("Оплата не найдена. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data == "my_purchases")
async def cb_my_purchases(callback: types.CallbackQuery):
    purchases = await get_user_purchases(callback.from_user.id)

    if not purchases:
        await callback.answer("У вас пока нет покупок", show_alert=True)
        return

    text = f'<b><tg-emoji emoji-id="5884479287171485878">📜</tg-emoji> Мои покупки</b>\n\n'
    for p in purchases:
        text += f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> {p["product_name"]} — {p["price"]:.0f}₽ ({p["purchased_at"][:10]})\n'

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button("main"))
    await callback.answer()


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
        [InlineKeyboardButton(text="Профиль", callback_data="setmedia_profile_menu", icon_custom_emoji_id="5870994129244131212")],
        [InlineKeyboardButton(text="О шопе", callback_data="setmedia_about_menu", icon_custom_emoji_id="6028435952299413210")],
        [InlineKeyboardButton(text="Поддержка", callback_data="setmedia_support_menu", icon_custom_emoji_id="6039486778597970865")],
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
                await bot.send_message(user_id, message.text, parse_mode="HTML")
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
    await state.update_data(description=message.text)
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
            [InlineKeyboardButton(text="Файловый (до 10 файлов)", callback_data="prodtype_file")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])

        await message.answer(
            '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Выберите тип товара:</blockquote>',
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except ValueError:
        await message.answer("Введите корректную цену (число)", parse_mode="HTML")


@router.callback_query(F.data.startswith("prodtype_"), AdminStates.add_product_type)
async def cb_prodtype(callback: types.CallbackQuery, state: FSMContext):
    prod_type = callback.data.split("_")[1]
    await state.update_data(product_type=prod_type, file_ids=[])

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
        await state.set_state(AdminStates.add_product_files)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data="finish_files")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])
        await callback.message.edit_text(
            '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Новый товар</b>\n\n<blockquote>Отправьте файлы товара (до 10 файлов).\nПосле отправки всех файлов нажмите "Готово":</blockquote>',
            parse_mode="HTML",
            reply_markup=keyboard
        )
    await callback.answer()


@router.message(AdminStates.add_product_files, F.document)
async def process_product_files(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("file_ids", [])
    
    if len(file_ids) >= 10:
        await message.answer("Достигнут лимит в 10 файлов. Нажмите 'Готово' для завершения.")
        return
    
    file_ids.append(message.document.file_id)
    await state.update_data(file_ids=file_ids)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="finish_files")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
    ])
    
    await message.answer(
        f'<b>✅ Файл добавлен!</b>\n\nЗагружено файлов: {len(file_ids)}/10\n\nОтправьте ещё файлы или нажмите "Готово"',
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "finish_files", AdminStates.add_product_files)
async def cb_finish_files(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("file_ids", [])
    
    if not file_ids:
        await callback.answer("Добавьте хотя бы один файл!", show_alert=True)
        return
    
    await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        'file',
        file_ids=file_ids
    )
    await state.clear()
    
    await callback.message.edit_text(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Товар успешно добавлен!</b>',
        parse_mode="HTML",
        reply_markup=admin_back()
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
    await set_shop_setting("shop_info", message.text)
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Описание магазина обновлено!</b>',
        parse_mode="HTML",
        reply_markup=admin_back()
    )


@router.callback_query(F.data.in_(["admin_panel", "admin_media", "admin_categories", "admin_products", "addprod", "admin_settings", "admin_users"]))
async def cancel_state(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()


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
