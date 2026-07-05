import asyncio
import logging
import math
import os
from datetime import date, datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.filters import Command, CommandStart
from aiogram.utils.deep_linking import decode_payload, create_start_link
import aiosqlite

# -------------------- КОНСТАНТЫ --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = "carrrotssbot"

# ID администраторов (задайте через переменную окружения ADMIN_IDS, разделяя запятыми)
# или укажите вручную ниже (замените 123456789 на свой Telegram ID)
ADMIN_IDS = [5235589433]
admin_ids_str = os.getenv("ADMIN_IDS")
if admin_ids_str:
    try:
        ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]
    except:
        pass
if not ADMIN_IDS:
    ADMIN_IDS = [5235589433]  # <--- ЗАМЕНИТЕ НА СВОЙ TELEGRAM ID
    logging.warning("ADMIN_IDS не задан в окружении, используется значение по умолчанию")

START_BONUS = 20
REFERRAL_BONUS = 50
REFERRAL_XP_BONUS = 100
TASK_XP = 10
DAILY_BASE = 10          # не используется, т.к. бонус теперь фиксированный
DAILY_MULTIPLIER = 5     # не используется
LEVEL_UP_REWARD = 20
CONVERSION_RATE = 1000
MIN_CONVERT_AMOUNT = 1000
XP_PER_LEVEL = 1000

REFERRAL_PERCENTS = {
    (1, 2): 5,
    (3, 5): 7,
    (6, 10): 10,
    (11, float('inf')): 12
}

SHOP_ITEMS = [
    {"name": "Смена имени", "price": 200, "description": "Позволяет изменить имя в профиле"},
    {"name": "Золотая рамка", "price": 500, "description": "Премиум-метка в профиле (без реального эффекта)"},
    {"name": "Бустер опыта", "price": 300, "description": "Увеличивает получаемый опыт за задания на 50% на 24 часа"},
    {"name": "Удвоитель морковок", "price": 1000, "description": "Следующее выполненное задание принесёт двойные морковки (одноразовый)"},
]

TASKS = [
    {"title": "Подписаться на канал @ggooddvvibess", "type": "subscribe_channel", "target": "@ggooddvvibess", "reward": 120},
    {"title": "Вступить в группу @piarrrvzz", "type": "join_group", "target": "@piarrrvzz", "reward": 100},
    {"title": "Нажми на кнопку", "type": "inline_button", "target": "Просто нажми", "reward": 25},
]

# -------------------- НАСТРОЙКА ЛОГГИРОВАНИЯ --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

router = Router()
db_path = "bot.db"

# -------------------- БАЗА ДАННЫХ --------------------
async def get_db() -> aiosqlite.Connection:
    return await aiosqlite.connect(db_path)

async def create_tables():
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                age INTEGER,
                city TEXT,
                balance_carrots INTEGER DEFAULT 0,
                balance_rub INTEGER DEFAULT 0,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                last_bonus_date TEXT,
                premium INTEGER DEFAULT 0,
                exp_boost_until TEXT,
                double_carrot_count INTEGER DEFAULT 0,
                total_earned_carrots INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referral_id INTEGER,
                PRIMARY KEY (referrer_id, referral_id),
                FOREIGN KEY(referrer_id) REFERENCES users(user_id),
                FOREIGN KEY(referral_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                type TEXT,
                target TEXT,
                reward INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS completed_tasks (
                user_id INTEGER,
                task_id INTEGER,
                completed_at TEXT,
                PRIMARY KEY (user_id, task_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                carrot_amount INTEGER,
                rub_amount INTEGER,
                timestamp TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()

async def populate_tasks():
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM tasks")
        count = (await cursor.fetchone())[0]
        if count == 0:
            for task in TASKS:
                await db.execute("INSERT INTO tasks (title, type, target, reward) VALUES (?, ?, ?, ?)",
                                 (task["title"], task["type"], task["target"], task["reward"]))
            await db.commit()
            logger.info("Таблица tasks заполнена начальными заданиями")

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def get_referral_percent(referral_count: int) -> int:
    for (low, high), percent in REFERRAL_PERCENTS.items():
        if low <= referral_count <= high:
            return percent
    return 0

async def get_user(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def add_user(user_id: int, name: str, age: int, city: str, referrer_id: Optional[int] = None):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO users (user_id, name, age, city, balance_carrots) VALUES (?, ?, ?, ?, ?)",
                         (user_id, name, age, city, START_BONUS))
        if referrer_id:
            cursor = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
            if await cursor.fetchone():
                await db.execute("INSERT INTO referrals (referrer_id, referral_id) VALUES (?, ?)",
                                 (referrer_id, user_id))
                await db.execute("UPDATE users SET balance_carrots = balance_carrots + ?, xp = xp + ? WHERE user_id = ?",
                                 (REFERRAL_BONUS, REFERRAL_XP_BONUS, referrer_id))
                await check_level_up(referrer_id)
        await db.commit()

async def update_user_balance(user_id: int, carrot_delta: int = 0, rub_delta: int = 0):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE users SET balance_carrots = balance_carrots + ?, balance_rub = balance_rub + ? WHERE user_id = ?",
                         (carrot_delta, rub_delta, user_id))
        await db.commit()

async def add_xp(user_id: int, amount: int):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE users SET xp = xp + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
    await check_level_up(user_id)

async def check_level_up(user_id: int):
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return
        xp, old_level = row
        new_level = int(math.sqrt(xp / XP_PER_LEVEL)) if xp >= XP_PER_LEVEL else 0
        if new_level > old_level:
            reward = LEVEL_UP_REWARD * new_level
            await db.execute("UPDATE users SET level = ?, balance_carrots = balance_carrots + ? WHERE user_id = ?",
                             (new_level, reward, user_id))
            await db.commit()

async def get_referrer(user_id: int) -> Optional[int]:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT referrer_id FROM referrals WHERE referral_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def get_referral_count(user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0

async def get_referral_list(user_id: int) -> list:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("""
            SELECT u.name, u.total_earned_carrots
            FROM referrals r
            JOIN users u ON r.referral_id = u.user_id
            WHERE r.referrer_id = ?
        """, (user_id,))
        rows = await cursor.fetchall()
        return [{"name": row[0], "earned": row[1]} for row in rows]

async def is_task_completed(user_id: int, task_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT 1 FROM completed_tasks WHERE user_id = ? AND task_id = ?", (user_id, task_id))
        return bool(await cursor.fetchone())

async def complete_task(user_id: int, task_id: int):
    async with aiosqlite.connect(db_path) as db:
        now = datetime.now().isoformat()
        await db.execute("INSERT INTO completed_tasks (user_id, task_id, completed_at) VALUES (?, ?, ?)",
                         (user_id, task_id, now))
        await db.commit()

async def get_task_reward(task_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT reward FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0

async def process_task_reward(user_id: int, task_id: int, base_reward: int) -> int:
    user = await get_user(user_id)
    if not user:
        return 0
    reward_multiplier = 1
    if user["double_carrot_count"] > 0:
        reward_multiplier = 2
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE users SET double_carrot_count = double_carrot_count - 1 WHERE user_id = ?", (user_id,))
            await db.commit()
    final_reward = math.floor(base_reward * reward_multiplier)
    await update_user_balance(user_id, carrot_delta=final_reward)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE users SET total_earned_carrots = total_earned_carrots + ? WHERE user_id = ?",
                         (final_reward, user_id))
        await db.commit()
    exp_amount = TASK_XP
    if user["exp_boost_until"]:
        try:
            boost_until = datetime.fromisoformat(user["exp_boost_until"])
            if datetime.now() < boost_until:
                exp_amount = math.floor(exp_amount * 1.5)
        except:
            pass
    await add_xp(user_id, exp_amount)
    referrer_id = await get_referrer(user_id)
    if referrer_id:
        ref_count = await get_referral_count(referrer_id)
        percent = get_referral_percent(ref_count)
        if percent > 0:
            ref_bonus = math.floor(final_reward * percent / 100)
            if ref_bonus > 0:
                await update_user_balance(referrer_id, carrot_delta=ref_bonus)
    return final_reward

# -------------------- FSM --------------------
class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_city = State()

class ChangeName(StatesGroup):
    waiting_for_new_name = State()

class ConvertState(StatesGroup):
    waiting_for_amount = State()

# FSM для админ-команд
class AdminStates(StatesGroup):
    waiting_for_task_data = State()

# -------------------- ОБРАБОТЧИКИ --------------------
@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    args = message.text.split()
    referrer_id = None
    if len(args) > 1:
        try:
            referrer_id = int(args[1])
        except ValueError:
            referrer_id = None

    existing_user = await get_user(user_id)
    if existing_user:
        await state.clear()
        await show_main_menu(message)
        return

    await state.update_data(referrer_id=referrer_id)
    await message.answer("Добро пожаловать! Давай познакомимся. Как тебя зовут?")
    await state.set_state(Registration.waiting_for_name)

@router.message(Registration.waiting_for_name, F.text)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("Имя не может быть пустым. Введи ещё раз:")
        return
    await state.update_data(name=name)
    await message.answer("Сколько тебе лет? (целое число)")
    await state.set_state(Registration.waiting_for_age)

@router.message(Registration.waiting_for_age, F.text)
async def process_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text)
        if age < 5 or age > 120:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введи корректный возраст (число от 5 до 120):")
        return
    await state.update_data(age=age)
    await message.answer("Из какого ты города?")
    await state.set_state(Registration.waiting_for_city)

@router.message(Registration.waiting_for_city, F.text)
async def process_city(message: types.Message, state: FSMContext, bot: Bot):
    city = message.text.strip()
    if not city:
        await message.answer("Город не может быть пустым. Введи ещё раз:")
        return
    user_data = await state.get_data()
    name = user_data["name"]
    age = user_data["age"]
    referrer_id = user_data.get("referrer_id")
    user_id = message.from_user.id

    await add_user(user_id, name, age, city, referrer_id)
    await state.clear()
    await message.answer(f"Регистрация завершена! Тебе начислено {START_BONUS} 🥕.")
    await show_main_menu(message)

def main_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🥕 Задания")],
        [KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="👥 Рефералы")],
        [KeyboardButton(text="🛒 Магазин")],
        [KeyboardButton(text="🎁 Ежедневный бонус")],
        [KeyboardButton(text="💱 Конвертировать")],
        [KeyboardButton(text="💳 Вывод")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

async def show_main_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard())

# -------------------- ПРОФИЛЬ --------------------
@router.message(F.text == "👤 Профиль")
async def profile(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    ref_count = await get_referral_count(user["user_id"])
    percent = get_referral_percent(ref_count)
    premium_status = "🌟 Премиум" if user["premium"] else "Обычный"
    boost_info = ""
    if user["exp_boost_until"]:
        try:
            until = datetime.fromisoformat(user["exp_boost_until"])
            if datetime.now() < until:
                boost_info = f"\nБустер опыта активен до {until.strftime('%d.%m.%Y %H:%M')}"
        except:
            pass
    double_info = ""
    if user["double_carrot_count"] > 0:
        double_info = f"\nУдвоителей морковок: {user['double_carrot_count']}"

    text = (
        f"👤 Имя: {user['name']}\n"
        f"🎂 Возраст: {user['age']}\n"
        f"🏙 Город: {user['city']}\n"
        f"⭐ Уровень: {user['level']}\n"
        f"✨ Опыт: {user['xp']}\n"
        f"🥕 Баланс: {user['balance_carrots']} 🥕\n"
        f"💳 Рублёвый баланс: {user['balance_rub']} руб.\n"
        f"🔗 Реферальный код: {user['user_id']}\n"
        f"👥 Рефералов: {ref_count} (доход {percent}%)\n"
        f"💎 Статус: {premium_status}{boost_info}{double_info}"
    )
    await message.answer(text)

# -------------------- ЕЖЕДНЕВНЫЙ БОНУС (изменён на 50) --------------------
@router.message(F.text == "🎁 Ежедневный бонус")
async def daily_bonus(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    today = date.today().isoformat()
    if user["last_bonus_date"] == today:
        await message.answer("Ты уже получил бонус сегодня. Приходи завтра!")
        return

    bonus = 50  # <--- ИЗМЕНЕНО: теперь всегда 50
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE users SET balance_carrots = balance_carrots + ?, last_bonus_date = ? WHERE user_id = ?",
                         (bonus, today, user["user_id"]))
        await db.commit()
    await message.answer(f"Ежедневный бонус: +{bonus} 🥕! Твой баланс: {user['balance_carrots'] + bonus} 🥕")

# -------------------- ЗАДАНИЯ --------------------
@router.message(F.text == "🥕 Задания")
async def tasks_list(message: types.Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT id, title, type, reward FROM tasks")
        tasks = await cursor.fetchall()
    if not tasks:
        await message.answer("Заданий пока нет.")
        return
    for task_id, title, task_type, reward in tasks:
        done = await is_task_completed(user_id, task_id)
        status = "✅" if done else "🔄"
        if not done:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"Выполнить ({reward} 🥕)", callback_data=f"do_task_{task_id}")]
            ])
        else:
            kb = None
        text = f"{status} {title} (Награда: {reward} 🥕)"
        await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("do_task_"))
async def process_task(callback: types.CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    task_id = int(callback.data.split("_")[-1])
    if await is_task_completed(user_id, task_id):
        await callback.answer("Задание уже выполнено.", show_alert=True)
        return
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT type, target, reward FROM tasks WHERE id = ?", (task_id,))
        task_row = await cursor.fetchone()
    if not task_row:
        await callback.answer("Задание не найдено.", show_alert=True)
        return
    task_type, target, reward = task_row

    if task_type == "inline_button":
        await complete_task(user_id, task_id)
        final_reward = await process_task_reward(user_id, task_id, reward)
        await callback.message.edit_text(f"Задание выполнено! Награда: {final_reward} 🥕")
        await callback.answer()
        return

    try:
        if task_type == "subscribe_channel":
            chat_member = await bot.get_chat_member(chat_id=target, user_id=user_id)
            if chat_member.status in ["member", "administrator", "creator"]:
                await complete_task(user_id, task_id)
                final_reward = await process_task_reward(user_id, task_id, reward)
                await callback.message.edit_text(f"Задание выполнено! Награда: {final_reward} 🥕")
            else:
                await callback.answer("Ты не подписан на канал.", show_alert=True)
        elif task_type == "join_group":
            chat_member = await bot.get_chat_member(chat_id=target, user_id=user_id)
            if chat_member.status in ["member", "administrator", "creator"]:
                await complete_task(user_id, task_id)
                final_reward = await process_task_reward(user_id, task_id, reward)
                await callback.message.edit_text(f"Задание выполнено! Награда: {final_reward} 🥕")
            else:
                await callback.answer("Ты не вступил в группу.", show_alert=True)
        else:
            await callback.answer("Неизвестный тип задания.", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        await callback.answer("Не удалось проверить подписку. Убедись, что бот имеет доступ к каналу/группе.", show_alert=True)

# -------------------- РЕФЕРАЛЫ --------------------
@router.message(F.text == "👥 Рефералы")
async def referrals(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    ref_count = await get_referral_count(user_id)
    percent = get_referral_percent(ref_count)
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    ref_list = await get_referral_list(user_id)
    text = (
        f"🔗 Твоя реферальная ссылка:\n{ref_link}\n"
        f"👥 Рефералов: {ref_count}\n"
        f"💰 Текущий процент: {percent}%\n"
    )
    if ref_list:
        text += "\nТвои рефералы:\n"
        for r in ref_list:
            text += f"• {r['name']} — заработано: {r['earned']} 🥕\n"
    await message.answer(text, disable_web_page_preview=True)

# -------------------- МАГАЗИН --------------------
@router.message(F.text == "🛒 Магазин")
async def shop(message: types.Message):
    kb_rows = []
    for item in SHOP_ITEMS:
        kb_rows.append([
            InlineKeyboardButton(text=f"{item['name']} — {item['price']} 🥕", callback_data=f"buy_{item['name']}")
        ])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer("Добро пожаловать в магазин! Выбери товар:", reply_markup=kb)

@router.callback_query(F.data.startswith("buy_"))
async def buy_item(callback: types.CallbackQuery):
    item_name = callback.data[4:]
    item = next((i for i in SHOP_ITEMS if i["name"] == item_name), None)
    if not item:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    user_id = callback.from_user.id
    user = await get_user(user_id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь.", show_alert=True)
        return
    if user["balance_carrots"] < item["price"]:
        await callback.answer(f"Недостаточно морковок! Нужно {item['price']}, у тебя {user['balance_carrots']}.", show_alert=True)
        return

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтвердить", callback_data=f"confirm_buy_{item_name}")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel_buy")]
    ])
    await callback.message.answer(
        f"Ты собираешься купить «{item['name']}» за {item['price']} 🥕.\n{item['description']}\nПодтверди покупку:",
        reply_markup=confirm_kb
    )
    await callback.answer()

@router.callback_query(F.data == "cancel_buy")
async def cancel_buy(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer("Покупка отменена.")

@router.callback_query(F.data.startswith("confirm_buy_"))
async def confirm_buy(callback: types.CallbackQuery, state: FSMContext):
    item_name = callback.data[len("confirm_buy_"):]
    item = next((i for i in SHOP_ITEMS if i["name"] == item_name), None)
    if not item:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    user_id = callback.from_user.id
    user = await get_user(user_id)
    if not user or user["balance_carrots"] < item["price"]:
        await callback.answer("Недостаточно средств.", show_alert=True)
        return

    await update_user_balance(user_id, carrot_delta=-item["price"])
    if item_name == "Смена имени":
        await callback.message.answer("Введи новое имя:")
        await state.set_state(ChangeName.waiting_for_new_name)
    elif item_name == "Золотая рамка":
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE users SET premium = 1 WHERE user_id = ?", (user_id,))
            await db.commit()
        await callback.message.answer("Ты приобрёл Золотую рамку! Статус Премиум активирован.")
    elif item_name == "Бустер опыта":
        until = datetime.now() + timedelta(hours=24)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE users SET exp_boost_until = ? WHERE user_id = ?",
                             (until.isoformat(), user_id))
            await db.commit()
        await callback.message.answer(f"Бустер опыта активирован! Действует до {until.strftime('%d.%m.%Y %H:%M')}.")
    elif item_name == "Удвоитель морковок":
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE users SET double_carrot_count = double_carrot_count + 1 WHERE user_id = ?",
                             (user_id,))
            await db.commit()
        await callback.message.answer("Удвоитель морковок активирован! Следующее задание принесёт двойную награду.")
    await callback.message.delete()
    await callback.answer("Покупка совершена!")

@router.message(ChangeName.waiting_for_new_name, F.text)
async def change_name_done(message: types.Message, state: FSMContext):
    new_name = message.text.strip()
    if not new_name:
        await message.answer("Имя не может быть пустым. Попробуй ещё раз:")
        return
    user_id = message.from_user.id
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE users SET name = ? WHERE user_id = ?", (new_name, user_id))
        await db.commit()
    await state.clear()
    await message.answer(f"Имя изменено на {new_name}!")

# -------------------- КОНВЕРТАЦИЯ --------------------
@router.message(F.text == "💱 Конвертировать")
async def convert_start(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    await message.answer(
        f"Курс: 1000 🥕 = {CONVERSION_RATE} руб.\n"
        f"Минимальная сумма: {MIN_CONVERT_AMOUNT} 🥕.\n"
        "Введи количество морковок для конвертации:"
    )
    await state.set_state(ConvertState.waiting_for_amount)

@router.message(ConvertState.waiting_for_amount, F.text)
async def convert_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введи целое число.")
        return
    if amount < MIN_CONVERT_AMOUNT:
        await message.answer(f"Минимальная сумма конвертации: {MIN_CONVERT_AMOUNT} 🥕.")
        return
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user["balance_carrots"] < amount:
        await message.answer(f"Недостаточно морковок. У тебя {user['balance_carrots']} 🥕.")
        return
    rub_amount = math.floor(amount * CONVERSION_RATE / 1000)
    if rub_amount <= 0:
        await message.answer("Сумма слишком мала для конвертации.")
        return
    await update_user_balance(user_id, carrot_delta=-amount, rub_delta=rub_amount)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO conversions (user_id, carrot_amount, rub_amount, timestamp) VALUES (?, ?, ?, ?)",
                         (user_id, amount, rub_amount, datetime.now().isoformat()))
        await db.commit()
    await state.clear()
    await message.answer(f"Конвертировано {amount} 🥕 → {rub_amount} руб.\nТекущий рублёвый баланс: {user['balance_rub'] + rub_amount} руб.")

# -------------------- ВЫВОД --------------------
@router.message(F.text == "💳 Вывод")
async def withdraw_check(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    ref_count = await get_referral_count(user["user_id"])
    rub_balance = user["balance_rub"]
    conditions = []
    if rub_balance > 0:
        conditions.append("✅ Рублёвый баланс > 0")
    else:
        conditions.append("❌ Рублёвый баланс = 0 (нужно сконвертировать 🥕 в рубли)")
    if ref_count >= 3:
        conditions.append(f"✅ Количество рефералов: {ref_count} (>= 3)")
    else:
        conditions.append(f"❌ Количество рефералов: {ref_count} (нужно минимум 3)")
    if rub_balance > 0 and ref_count >= 3:
        text = "✅ Условия соблюдены! Вывод средств станет доступен в ближайшем обновлении. Ожидайте."
    else:
        text = "Не все условия выполнены:\n" + "\n".join(conditions)
    await message.answer(text)

# -------------------- АДМИНИСТРАТИВНЫЕ КОМАНДЫ (ДОБАВЛЕНЫ) --------------------
# Проверка прав администратора
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# /add_task – запускает диалог добавления задания
@router.message(Command("add_task"))
async def add_task_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав на эту команду.")
        return
    await message.answer(
        "📝 Введите данные нового задания в формате:\n\n"
        "`Название | тип | цель | награда`\n\n"
        "Пример:\n"
        "`Подпишись на канал | subscribe_channel | @channel | 100`\n\n"
        "Доступные типы: `subscribe_channel`, `join_group`, `inline_button`"
    )
    await state.set_state(AdminStates.waiting_for_task_data)

# Обработка введённых данных для добавления задания
@router.message(AdminStates.waiting_for_task_data, F.text)
async def add_task_process(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав.")
        await state.clear()
        return
    try:
        parts = message.text.split('|')
        if len(parts) != 4:
            raise ValueError("Неверный формат. Нужно 4 части, разделённые |")
        title = parts[0].strip()
        task_type = parts[1].strip()
        target = parts[2].strip()
        reward = int(parts[3].strip())
        if not title or not task_type or not target:
            raise ValueError("Название, тип и цель не могут быть пустыми")
        if task_type not in ["subscribe_channel", "join_group", "inline_button"]:
            raise ValueError("Неизвестный тип задания")
        if reward <= 0:
            raise ValueError("Награда должна быть положительным числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте ещё раз или отмените командой /cancel")
        return

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO tasks (title, type, target, reward) VALUES (?, ?, ?, ?)",
            (title, task_type, target, reward)
        )
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Задание «{title}» успешно добавлено!")

# /list_tasks – список всех заданий
@router.message(Command("list_tasks"))
async def list_tasks(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав.")
        return
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT id, title, reward FROM tasks ORDER BY id")
        rows = await cursor.fetchall()
    if not rows:
        await message.answer("📭 Заданий пока нет.")
        return
    text = "📋 Список заданий:\n\n"
    for row in rows:
        text += f"ID: {row[0]} | {row[1]} | Награда: {row[2]} 🥕\n"
    await message.answer(text)

# /remove_task <id> – удаление задания по ID
@router.message(Command("remove_task"))
async def remove_task(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав.")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Укажите ID задания: `/remove_task 5`")
        return
    try:
        task_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return
    async with aiosqlite.connect(db_path) as db:
        # Проверим, существует ли задание
        cursor = await db.execute("SELECT title FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer(f"❌ Задание с ID {task_id} не найдено.")
            return
        title = row[0]
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
    await message.answer(f"✅ Задание «{title}» (ID {task_id}) удалено.")

# /cancel – отмена текущего действия (для администратора)
@router.message(Command("cancel"))
async def cancel_admin(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного действия.")
        return
    await state.clear()
    await message.answer("❌ Действие отменено.")

# -------------------- НЕИЗВЕСТНЫЕ СООБЩЕНИЯ --------------------
@router.message()
async def unknown(message: types.Message, state: FSMContext):
    # Ничего не делаем — во время анкеты не мешаем, в остальном тоже не спамим
    pass

# -------------------- ЗАПУСК --------------------
async def main():
    await create_tables()
    await populate_tasks()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())