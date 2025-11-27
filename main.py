import asyncio
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.filters import Command

# ---------------- БАЗОВЫЕ НАСТРОЙКИ ----------------

# ВСТАВЬ СЮДА НОВЫЙ ТОКЕН БОТА
TOKEN = "8291523782:AAGu_N1gWHC6jPDiV0FeZQziC00gmePRr8g"

# ID ГРУППЫ, КУДА ЛЕТЯТ ЗАКАЗЫ
ADMIN_CHAT_ID = -1003224521766

# ID АДМИНА (ТВОЙ ЛИЧНЫЙ ID, ДЛЯ ПОДТВЕРЖДЕНИЯ ОПЛАТЫ)
ADMIN_USER_ID = 7804231004

# КРИПТО-КОШЕЛЬКИ
USDT_TRON_ADDRESS = "TKv25h36BJpggHTDwUt2yTbdasy5oJxihM"
TON_ADDRESS = "UQAubCPzsGgaFRWem_uaNLB8dD6oWHen_c80FQRSDfFi14CT"

# КУРСЫ ДЛЯ ПРИМЕРНОГО ПЕРЕСЧЁТА (ПРАВЬ ПОД СЕБЯ)
UAH_PER_USDT = 40.0   # 1 USDT ≈ 40 грн
UAH_PER_TON = 90.0    # 1 TON ≈ 90 грн

bot = Bot(TOKEN)
dp = Dispatcher()

DB_PATH = "orders.db"

# ---------------- БД ----------------


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Добавляем колонку user_order_number, если её нет
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            total_uah REAL,
            status TEXT,
            pay_currency TEXT,
            pay_amount REAL,
            created_at TEXT,
            user_order_number INTEGER
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            product_id TEXT,
            name TEXT,
            qty INTEGER,
            price_uah REAL
        )
        """
    )
    conn.commit()
    conn.close()


def create_order_in_db(user_id: int, username: str, cart_data: dict) -> int:
    """Создать заказ в БД, вернуть order_id."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Считаем сумму
    total_uah = 0
    for pid, qty in cart_data.items():
        product = ALL_PRODUCTS.get(pid)
        if not product:
            continue
        total_uah += product["price"] * qty

    # Определяем персональный номер заказа для этого пользователя
    cur.execute(
        "SELECT MAX(user_order_number) FROM orders WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    last_num = row[0] if row and row[0] is not None else 0
    user_order_number = last_num + 1

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO orders (user_id, username, total_uah, status, pay_currency, pay_amount, created_at, user_order_number)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, username, total_uah, "NEW", None, None, created_at, user_order_number),
    )
    order_id = cur.lastrowid

    for pid, qty in cart_data.items():
        product = ALL_PRODUCTS.get(pid)
        if not product:
            continue
        cur.execute(
            """
            INSERT INTO order_items (order_id, product_id, name, qty, price_uah)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, pid, product["name"], qty, product["price"]),
        )

    conn.commit()
    conn.close()
    return order_id


def update_order_status(order_id: int, status: str,
                        pay_currency: str | None = None,
                        pay_amount: float | None = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if pay_currency is not None and pay_amount is not None:
        cur.execute(
            "UPDATE orders SET status = ?, pay_currency = ?, pay_amount = ? WHERE id = ?",
            (status, pay_currency, pay_amount, order_id),
        )
    else:
        cur.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()


def get_order(order_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, username, total_uah, status, pay_currency, pay_amount,
               created_at, user_order_number
        FROM orders WHERE id = ?
        """,
        (order_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_order_items(order_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT name, qty, price_uah FROM order_items WHERE order_id = ?",
        (order_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_user_orders(user_id: int, limit: int = 5):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, total_uah, status, created_at, pay_currency, pay_amount, user_order_number
        FROM orders
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------- НАСТРОЙКИ ПОЛЬЗОВАТЕЛЕЙ ----------------

user_settings: dict[int, dict] = {}
cart: dict[int, dict[str, int]] = {}

CURRENCIES = {
    "UAH": {"symbol": "₴", "name": "грн", "rate": 1.0},
    "RUB": {"symbol": "₽", "name": "₽", "rate": 2.5},
    "USD": {"symbol": "$", "name": "$", "rate": 0.027},
}

LANG_TO_CURRENCY = {"ru": "RUB", "uk": "UAH", "en": "USD"}


def detect_lang_code(raw_code: str | None) -> str:
    if not raw_code:
        return "ru"
    code = raw_code.lower()[:2]
    return code if code in ("ru", "uk", "en") else "ru"


def set_user_lang_and_currency(user_id: int, lang: str):
    if lang not in ("ru", "uk", "en"):
        lang = "ru"
    currency = LANG_TO_CURRENCY.get(lang, "UAH")
    user_settings[user_id] = {"lang": lang, "currency": currency}


def get_user_currency(user_id: int) -> str:
    code = user_settings.get(user_id, {}).get("currency", "UAH")
    return code if code in CURRENCIES else "UAH"


def price_text_for_user(user_id: int, base_uah: float) -> str:
    code = get_user_currency(user_id)
    info = CURRENCIES[code]
    value = round(base_uah * info["rate"], 2)
    if code == "UAH":
        return f"{int(base_uah)} грн"
    return f"{value} {info['symbol']} (≈ {int(base_uah)} грн)"


def add_to_cart(user_id: int, product_id: str):
    cart.setdefault(user_id, {})
    cart[user_id][product_id] = cart[user_id].get(product_id, 0) + 1


def format_cart(user_id: int) -> str:
    if user_id not in cart or not cart[user_id]:
        return "🧺 Твоя корзина пуста."
    lines = []
    total_uah = 0
    for pid, qty in cart[user_id].items():
        product = ALL_PRODUCTS.get(pid)
        if not product:
            continue
        subtotal = product["price"] * qty
        total_uah += subtotal
        lines.append(
            f"{product['name']} x{qty} — {price_text_for_user(user_id, subtotal)}"
        )
    total_text = price_text_for_user(user_id, total_uah)
    text = "🧺 Твоя корзина:\n\n" + "\n".join(lines)
    text += (
        f"\n\nИтого: {total_text}\n\n"
        "‼️ Цены базируются в грн и могут немного меняться в зависимости от GEO и курса.\n"
        "⚠️ Время отлеги аккаунта может отличаться в зависимости от конкретного аккаунта.\n"
        "👨‍💻 Менеджер: @Accprestige"
    )
    return text


# ---------------- КНОПКИ ----------------

def build_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🛍 Каталог"),
                KeyboardButton(text="🧺 Корзина"),
            ],
            [
                KeyboardButton(text="📜 История заказов"),
            ],
            [
                KeyboardButton(text="ℹ О нас"),
                KeyboardButton(text="📞 Поддержка"),
            ],
            [
                KeyboardButton(text="⚙️ Настройки"),
            ]
        ],
        resize_keyboard=True,
    )


def build_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang:uk"),
            ],
            [
                InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
            ],
        ]
    )


# ---------------- ТОВАРЫ ----------------
# Все цены в грн

telegram_products = {
    "tg_colombia": {"name": "📲 Telegram 🇨🇴 Колумбия", "price": 55},
    "tg_usa": {"name": "📲 Telegram 🇺🇸 США", "price": 50},
    "tg_uk": {"name": "📲 Telegram 🇬🇧 Великобритания", "price": 85},
    "tg_brazil": {"name": "📲 Telegram 🇧🇷 Бразилия", "price": 110},
    "tg_canada": {"name": "📲 Telegram 🇨🇦 Канада", "price": 120},
    "tg_india": {"name": "📲 Telegram 🇮🇳 Индия", "price": 115},
    "tg_ukraine": {"name": "📲 Telegram 🇺🇦 Украина", "price": 230},
    "tg_philippines": {"name": "📲 Telegram 🇵🇭 Филиппины", "price": 120},
    "tg_myanmar": {"name": "📲 Telegram 🇲🇲 Мьянма", "price": 70},
    "tg_egypt": {"name": "📲 Telegram 🇪🇬 Египет", "price": 120},
    "tg_spain": {"name": "📲 Telegram 🇪🇸 Испания", "price": 115},
    "tg_morocco": {"name": "📲 Telegram 🇲🇦 Марокко (365+ дней)", "price": 250},
    "tg_indonesia": {"name": "📲 Telegram 🇮🇩 Индонезия", "price": 90},
    "tg_bangladesh": {"name": "📲 Telegram 🇧🇩 Бангладеш", "price": 70},
    "tg_russia": {"name": "📲 Telegram 🇷🇺 Россия", "price": 230},
}

# Новая категория: Telegram с большой отлегой
telegram_long_products = {
    "tg_long_nigeria_2023": {
        "name": "⏳ Telegram 🇳🇬 Нигерия (отлега, 2023 год)",
        "price": 410,
    },
    "tg_long_pakistan_2024": {
        "name": "⏳ Telegram 🇵🇰 Пакистан (отлега, 2024 год)",
        "price": 310,
    },
    "tg_long_pakistan_2023": {
        "name": "⏳ Telegram 🇵🇰 Пакистан (отлега, 2023 год)",
        "price": 420,
    },
    "tg_long_greece": {
        "name": "⏳ Telegram 🇬🇷 Греция (большая отлега)",
        "price": 200,
    },
    "tg_long_argentina": {
        "name": "⏳ Telegram 🇦🇷 Аргентина (большая отлега)",
        "price": 100,
    },
    "tg_long_turkey": {
        "name": "⏳ Telegram 🇹🇷 Турция (большая отлега)",
        "price": 150,
    },
}

tiktok_products = {
    "tt_empty": {"name": "🎵 TikTok пустой (0 пдп, 0 видео)", "price": 15},
    "tt_150": {"name": "🎵 TikTok 150+ пдп, 18 видео", "price": 60},
    "tt_1000": {"name": "🎵 TikTok 1000+ пдп, 0 видео", "price": 230},
    "tt_2000": {"name": "🎵 TikTok 2000+ пдп, без активности", "price": 300},
    "tt_business": {"name": "🎵 TikTok пустой бизнес-аккаунт", "price": 70},
    "tt_7000": {"name": "🎵 TikTok 7000+ пдп", "price": 500},
    "tt_10000": {"name": "🎵 TikTok 10000+ пдп, монетизация", "price": 800},
}

instagram_products = {
    "ig_new": {"name": "📸 Instagram новый (0 пдп, 0 постов)", "price": 20},
    "ig_7days": {"name": "📸 Instagram 7+ дней, 5+ постов", "price": 80},
    "ig_14days_100": {"name": "📸 Instagram 14+ дней, 100+ пдп", "price": 95},
    "ig_1month": {"name": "📸 Instagram 1+ месяц отлеги", "price": 60},
    "ig_150days_clean": {"name": "📸 Instagram 150+ дней отлеги (чистый)", "price": 75},
    "ig_5months": {"name": "📸 Instagram 5+ месяцев отлеги", "price": 90},
    "ig_150subs": {"name": "📸 Instagram 150+ подписчиков", "price": 80},
    "ig_250subs": {"name": "📸 Instagram 250+ подписчиков", "price": 90},
    "ig_6_12months": {"name": "📸 Instagram 6–12 месяцев отлеги", "price": 100},
    "ig_1year_90subs": {"name": "📸 Instagram 1+ год, 90+ пдп", "price": 220},
    "ig_1year_60posts": {"name": "📸 Instagram 1+ год, 60+ постов", "price": 280},
    "ig_2015": {"name": "📸 Instagram 2015 год", "price": 350},
    "ig_5000subs": {"name": "📸 Instagram 5000+ пдп, 2 года отлеги", "price": 1000},
    "ig_2013": {"name": "📸 Instagram 2013 год", "price": 600},
    "ig_9000subs": {"name": "📸 Instagram 9000+ пдп, 2.5+ года отлеги", "price": 850},
}

ALL_PRODUCTS: dict[str, dict] = {}
ALL_PRODUCTS.update(telegram_products)
ALL_PRODUCTS.update(telegram_long_products)
ALL_PRODUCTS.update(tiktok_products)
ALL_PRODUCTS.update(instagram_products)


# ---------------- /start ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    lang_code = detect_lang_code(message.from_user.language_code)
    set_user_lang_and_currency(user_id, lang_code)

    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Добро пожаловать в магазин виртуальных номеров и аккаунтов.\n"
        f"Сначала выбери язык интерфейса:",
        reply_markup=build_language_keyboard(),
    )


# ---------------- выбор языка ----------------

@dp.callback_query(F.data.startswith("lang:"))
async def cb_lang(callback: CallbackQuery):
    lang = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    set_user_lang_and_currency(user_id, lang)

    curr = get_user_currency(user_id)

    await callback.message.answer(
        f"✅ Язык установлен: {lang.upper()}\n"
        f"💱 Валюта отображения цен: {curr}\n\n"
        f"Используй меню ниже:",
        reply_markup=build_main_menu(),
    )
    await callback.answer()


# ---------------- главное меню ----------------

@dp.message()
async def main_menu_handler(message: Message):
    user_id = message.from_user.id
    text = message.text

    if text == "🛍 Каталог":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📲 Telegram аккаунты", callback_data="cat:telegram"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⏳ Telegram с большой отлегой", callback_data="cat:telegram_long"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🎵 TikTok аккаунты", callback_data="cat:tiktok"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📸 Instagram аккаунты", callback_data="cat:instagram"
                    )
                ],
            ]
        )
        await message.answer("Выбери категорию:", reply_markup=kb)

    elif text == "🧺 Корзина":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Оформить заказ", callback_data="order:create"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 Очистить корзину", callback_data="cart:clear"
                    )
                ],
            ]
        )
        await message.answer(format_cart(user_id), reply_markup=kb)

    elif text == "📜 История заказов":
        orders = get_user_orders(user_id)
        if not orders:
            await message.answer("У тебя пока нет заказов.")
        else:
            lines = ["📜 Последние заказы:\n"]
            for oid, total_uah, status, created_at, pay_curr, pay_amount, user_ord_num in orders:
                base = f"Заказ №{user_ord_num} от {created_at} — {int(total_uah)} грн"
                if pay_curr and pay_amount:
                    base += f" (оплата: {round(pay_amount, 4)} {pay_curr})"
                base += f"\nСтатус: {status}\n"
                lines.append(base)
            await message.answer("\n".join(lines))

    elif text == "ℹ О нас":
        await message.answer(
            "Это бот-магазин виртуальных номеров и аккаунтов Telegram / TikTok / Instagram.\n\n"
            "Все цены указаны в грн и могут немного меняться в зависимости от GEO и курса.\n"
            "⚠️ Время отлеги аккаунта может отличаться в зависимости от конкретного аккаунта.\n"
            "👨‍💻 Менеджер: @Accprestige"
        )

    elif text == "📞 Поддержка":
        await message.answer("По любым вопросам пиши менеджеру: @Accprestige")

    elif text == "⚙️ Настройки":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌐 Сменить язык", callback_data="settings:lang"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💱 Сменить валюту", callback_data="settings:curr"
                    )
                ],
            ]
        )
        await message.answer("⚙️ Настройки:", reply_markup=kb)

    else:
        await message.answer("Пожалуйста, используй кнопки меню снизу.")


# ---------------- настройки ----------------

@dp.callback_query(F.data == "settings:lang")
async def settings_lang(callback: CallbackQuery):
    await callback.message.answer(
        "Выбери язык:", reply_markup=build_language_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "settings:curr")
async def settings_curr(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="₴ UAH", callback_data="curr:UAH"),
                InlineKeyboardButton(text="₽ RUB", callback_data="curr:RUB"),
            ],
            [
                InlineKeyboardButton(text="$ USD", callback_data="curr:USD"),
            ],
        ]
    )
    await callback.message.answer("Выбери валюту отображения цен:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("curr:"))
async def cb_curr(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    if user_id not in user_settings:
        set_user_lang_and_currency(user_id, "ru")
    user_settings[user_id]["currency"] = code
    await callback.message.answer(f"✅ Валюта изменена на {code}")
    await callback.answer()


# ---------------- категории ----------------

@dp.callback_query(F.data == "cat:telegram")
async def cat_telegram(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=p["name"], callback_data=f"product:{pid}"
                )
            ]
            for pid, p in telegram_products.items()
        ]
    )
    await callback.message.answer("Выбери GEO для Telegram аккаунта:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "cat:telegram_long")
async def cat_telegram_long(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=p["name"], callback_data=f"product:{pid}"
                )
            ]
            for pid, p in telegram_long_products.items()
        ]
    )
    await callback.message.answer(
        "Аккаунты Telegram с большой отлегой:\nВыбери вариант:", reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(F.data == "cat:tiktok")
async def cat_tiktok(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=p["name"], callback_data=f"product:{pid}"
                )
            ]
            for pid, p in tiktok_products.items()
        ]
    )
    await callback.message.answer("Выбери TikTok аккаунт:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "cat:instagram")
async def cat_instagram(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=p["name"], callback_data=f"product:{pid}"
                )
            ]
            for pid, p in instagram_products.items()
        ]
    )
    await callback.message.answer("Выбери Instagram аккаунт:", reply_markup=kb)
    await callback.answer()


# ---------------- карточка товара ----------------

@dp.callback_query(F.data.startswith("product:"))
async def cb_product(callback: CallbackQuery):
    user_id = callback.from_user.id
    pid = callback.data.split(":", 1)[1]
    product = ALL_PRODUCTS.get(pid)

    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    base_price = product["price"]
    text = (
        f"<b>{product['name']}</b>\n"
        f"Цена: {price_text_for_user(user_id, base_price)}\n\n"
        f"⚠️ Время отлеги аккаунта может отличаться в зависимости от конкретного аккаунта.\n"
        f"‼️ Цена может меняться в зависимости от GEO и курса.\n"
        f"👨‍💻 Менеджер: @Accprestige"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧺 Добавить в корзину", callback_data=f"cart:add:{pid}"
                )
            ]
        ]
    )
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("cart:add:"))
async def cb_cart_add(callback: CallbackQuery):
    user_id = callback.from_user.id
    pid = callback.data.split(":", 2)[2]
    if pid not in ALL_PRODUCTS:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    add_to_cart(user_id, pid)
    await callback.message.answer("✅ Товар добавлен в корзину.")
    await callback.answer()


@dp.callback_query(F.data == "cart:clear")
async def cb_cart_clear(callback: CallbackQuery):
    user_id = callback.from_user.id
    cart[user_id] = {}
    await callback.message.answer("🗑 Корзина очищена.")
    await callback.answer()


# ---------------- оформление заказа ----------------

def format_order_items_text(order_id: int) -> str:
    items = get_order_items(order_id)
    if not items:
        return "—"
    lines = []
    for name, qty, price_uah in items:
        lines.append(f"{name} x{qty} — {int(price_uah)} грн за шт.")
    return "\n".join(lines)


@dp.callback_query(F.data == "order:create")
async def cb_order_create(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in cart or not cart[user_id]:
        await callback.answer("Корзина пуста.", show_alert=True)
        return

    username = callback.from_user.username or "нет_username"
    order_id = create_order_in_db(user_id, username, cart[user_id])

    # очищаем корзину после создания заказа
    cart[user_id] = {}

    order = get_order(order_id)
    total_uah = order[3]
    user_order_number = order[8]

    items_text = format_order_items_text(order_id)

    # Сообщение пользователю
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="USDT (TRC20)", callback_data=f"pay:USDT:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="TON", callback_data=f"pay:TON:{order_id}"
                )
            ],
        ]
    )
    await callback.message.answer(
        f"✅ Заказ №{user_order_number} создан.\n"
        f"Сумма: {int(total_uah)} грн.\n\n"
        f"Товары:\n{items_text}\n\n"
        f"Выбери способ оплаты:",
        reply_markup=kb,
    )

    # Сообщение в админ-группу
    await bot.send_message(
        ADMIN_CHAT_ID,
        f"🆕 Новый заказ №{user_order_number} (ID в БД: {order_id})\n"
        f"👤 Пользователь: @{username} (ID: {user_id})\n\n"
        f"🛍 Товары:\n{items_text}\n\n"
        f"💰 Сумма: {int(total_uah)} грн\n"
        f"Статус: NEW (ожидает выбор способа оплаты)",
    )

    update_order_status(order_id, "NEW")
    await callback.answer()


# ---------------- выбор криптовалюты ----------------

@dp.callback_query(F.data.startswith("pay:"))
async def cb_pay_choose(callback: CallbackQuery):
    parts = callback.data.split(":")
    pay_curr = parts[1]  # USDT или TON
    order_id = int(parts[2])

    order = get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    total_uah = order[3]
    user_order_number = order[8]

    if pay_curr == "USDT":
        amount = round(total_uah / UAH_PER_USDT, 4)
        address = USDT_TRON_ADDRESS
        network = "TRON (TRC20)"
        explorer_url = f"https://tronscan.org/#/address/{address}"
    else:
        amount = round(total_uah / UAH_PER_TON, 4)
        address = TON_ADDRESS
        network = "TON"
        explorer_url = f"https://tonviewer.com/{address}"

    # Обновляем статус заказа
    update_order_status(order_id, "WAITING_PAYMENT", pay_curr, amount)

    text = (
        f"💳 Оплата заказа №{user_order_number}\n"
        f"Сумма: {int(total_uah)} грн ≈ {amount} {pay_curr}\n\n"
        f"Сеть: {network}\n"
        f"Адрес для оплаты:\n"
        f"<a href='{explorer_url}'>{address}</a>\n\n"
        f"Нажми на адрес или кнопку ниже, чтобы открыть его в проводнике.\n"
        f"После перевода нажми «✅ Я оплатил»."
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Открыть адрес",
                    url=explorer_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Я оплатил",
                    callback_data=f"paid:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить заказ",
                    callback_data=f"order:cancel:{order_id}"
                )
            ]
        ]
    )

    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    items_text = format_order_items_text(order_id)

    await bot.send_message(
        ADMIN_CHAT_ID,
        f"💳 Заказ №{user_order_number} (ID в БД: {order_id}) ожидает оплату в {pay_curr}.\n"
        f"Сумма: {int(total_uah)} грн ≈ {amount} {pay_curr}\n\n"
        f"🛍 Товары:\n{items_text}",
    )

    await callback.answer()


# ---------------- пользователь нажал «Я оплатил» ----------------

@dp.callback_query(F.data.startswith("paid:"))
async def cb_paid(callback: CallbackQuery):
    order_id = int(callback.data.split(":", 1)[1])
    order = get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    update_order_status(order_id, "WAITING_CONFIRMATION")

    user_id = order[1]
    username = order[2]
    total_uah = order[3]
    pay_curr = order[5]
    pay_amount = order[6]
    user_order_number = order[8]

    items_text = format_order_items_text(order_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить оплату", callback_data=f"admin:confirm:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить заказ", callback_data=f"admin:cancel:{order_id}"
                )
            ],
        ]
    )
    await bot.send_message(
        ADMIN_CHAT_ID,
        f"🕓 Пользователь сообщил об оплате заказа №{user_order_number} (ID в БД: {order_id}).\n"
        f"👤 @{username} (ID: {user_id})\n"
        f"Сумма: {int(total_uah)} грн ≈ {pay_amount} {pay_curr}\n\n"
        f"🛍 Товары:\n{items_text}",
        reply_markup=kb,
    )

    await callback.message.answer(
        "✅ Спасибо! Мы получили уведомление об оплате.\n"
        "Менеджер проверит перевод и подтвердит заказ."
    )
    await callback.answer()


# ---------------- админ: подтвердить / отменить ----------------

@dp.callback_query(F.data.startswith("admin:"))
async def cb_admin(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_USER_ID:
        await callback.answer("У тебя нет прав подтверждать оплату.", show_alert=True)
        return

    parts = callback.data.split(":")
    action = parts[1]   # confirm / cancel
    order_id = int(parts[2])

    order = get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    user_id = order[1]
    username = order[2]
    total_uah = order[3]
    pay_curr = order[5]
    pay_amount = order[6]
    user_order_number = order[8]

    items_text = format_order_items_text(order_id)

    if action == "confirm":
        update_order_status(order_id, "PAID")
        try:
            await bot.send_message(
                user_id,
                f"✅ Оплата заказа №{user_order_number} подтверждена!\n"
                f"Сумма: {int(total_uah)} грн ≈ {pay_amount} {pay_curr}\n"
                f"Менеджер скоро свяжется с тобой.",
            )
        except Exception:
            pass

        await callback.message.edit_text(
            f"💰 Оплата подтверждена админом для заказа №{user_order_number} (ID в БД: {order_id}).\n"
            f"👤 @{username}\n"
            f"Сумма: {int(total_uah)} грн ≈ {pay_amount} {pay_curr}\n\n"
            f"🛍 Товары:\n{items_text}"
        )
        await callback.answer("Оплата подтверждена.")

    elif action == "cancel":
        update_order_status(order_id, "CANCELED")
        try:
            await bot.send_message(
                user_id,
                f"❌ Заказ №{user_order_number} был отменён администратором.\n"
                f"Если это ошибка — напиши менеджеру: @Accprestige",
            )
        except Exception:
            pass

        await callback.message.edit_text(
            f"❌ Заказ №{user_order_number} (ID в БД: {order_id}) отменён админом.\n"
            f"👤 @{username}\n"
            f"Сумма: {int(total_uah)} грн ≈ {pay_amount} {pay_curr}\n\n"
            f"🛍 Товары:\n{items_text}"
        )
        await callback.answer("Заказ отменён.")


@dp.callback_query(F.data.startswith("order:cancel:"))
async def cb_order_cancel(callback: CallbackQuery):
    order_id = int(callback.data.split(":", 2)[2])
    order = get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    user_order_number = order[8]
    update_order_status(order_id, "CANCELED")
    await callback.message.answer(
        f"Заказ №{user_order_number} отменён. Если нужно — можешь оформить новый."
    )
    await callback.answer()


# ---------------- запуск ----------------

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
