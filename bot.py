import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import Message, CallbackQuery, LinkPreviewOptions
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, ADMIN_ID, SUPPORT_CHAT_ID
from database import (
    init_db, upsert_user, get_user, create_ticket,
    get_all_tournaments, get_tournament, count_registrations,
    register_for_tournament, unregister_from_tournament,
    is_registered, get_user_registrations, add_tournament
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

SUPPORT_USERS: set[int] = set()


# ---------- Машина состояний ----------
class TournamentCreation(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_start_at = State()
    waiting_for_max_players = State()


class AdminReplyState(StatesGroup):
    waiting_for_answer = State()


# ---------- Клавиатуры ----------
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏆 Турниры", callback_data="menu:tournaments")
    kb.button(text="👤 Профиль", callback_data="menu:profile")
    kb.button(text="📜 Правила", callback_data="menu:rules")
    kb.button(text="🛠 Техподдержка", callback_data="menu:support")
    kb.adjust(2, 2)
    return kb.as_markup()


def tournaments_list_kb(tournaments, is_admin: bool):
    kb = InlineKeyboardBuilder()
    for t in tournaments:
        kb.button(text=f"🏅 {t['name']}", callback_data=f"tour:{t['id']}")
    
    if is_admin:
        kb.button(text="➕ Создать турнир", callback_data="admin:create_tournament")
        
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def tournament_detail_kb(tournament_id: int, registered: bool, is_open: bool):
    kb = InlineKeyboardBuilder()
    if is_open:
        if registered:
            kb.button(text="❌ Отменить регистрацию", callback_data=f"unreg:{tournament_id}")
        else:
            kb.button(text="✅ Зарегистрироваться", callback_data=f"reg:{tournament_id}")
    kb.button(text="⬅️ К турнирам", callback_data="menu:tournaments")
    kb.adjust(1)
    return kb.as_markup()


# ---------- /start и меню ----------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await upsert_user(message.from_user.id,
                      message.from_user.username or "",
                      message.from_user.first_name or "")
    await message.answer(
        f"Привет, <b>{message.from_user.first_name}</b>! 👋\n"
        "Я бот турнирной платформы. Выбери раздел:",
        reply_markup=main_menu_kb()
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_menu_kb())


@dp.callback_query(F.data == "menu:main")
async def cb_main(event: CallbackQuery):
    await event.message.edit_text(
        "Главное меню. Выбери раздел:",
        reply_markup=main_menu_kb()
    )
    await event.answer()


# ---------- Профиль ----------
@dp.callback_query(F.data == "menu:profile")
@dp.message(Command("profile"))
async def show_profile(event):
    uid = event.from_user.id
    user = await get_user(uid)
    if not user:
        await upsert_user(uid, event.from_user.username or "", event.from_user.first_name or "")
        user = await get_user(uid)

    regs = await get_user_registrations(uid)
    username = f"@{user['username']}" if user["username"] else "не указан"

    text = (
        "<b>👤 Твой профиль</b>\n\n"
        f"🆔 <b>ID игрока:</b> <code>{user['user_id']}</code>\n"
        f"📛 <b>Ник:</b> {username}\n"
        f"🏅 <b>Сыграно турниров:</b> {user['tournaments_played']}\n\n"
    )
    if regs:
        text += "<b>📝 Мои регистрации:</b>\n"
        for r in regs:
            status = "🟢" if r["status"] == "open" else "🔴"
            text += f"• {status} {r['name']} — <i>{r['start_at']}</i>\n"
    else:
        text += "<i>Ты ещё не зарегистрирован ни на один турнир.</i>"

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=main_menu_kb())
        await event.answer()
    else:
        await event.answer(text, reply_markup=main_menu_kb())


# ---------- Турниры: список ----------
@dp.callback_query(F.data == "menu:tournaments")
@dp.message(Command("tournaments"))
async def show_tournaments(event):
    tournaments = await get_all_tournaments()
    is_admin = (event.from_user.id == ADMIN_ID)
    
    if not tournaments:
        text = "<b>🏆 Турниры</b>\n\nПока нет активных турниров."
        kb = main_menu_kb()
    else:
        text = "<b>🏆 Турниры</b>\n\nВыбери турнир, чтобы узнать подробности и зарегистрироваться:"
        kb = tournaments_list_kb(tournaments, is_admin)

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


# ---------- Турнир: детали ----------
@dp.callback_query(F.data.startswith("tour:"))
async def show_tournament_detail(event: CallbackQuery):
    tid = int(event.data.split(":", 1)[1])
    t = await get_tournament(tid)
    if not t:
        await event.answer("Турнир не найден.", show_alert=True)
        return

    registered = await is_registered(tid, event.from_user.id)
    players_count = await count_registrations(tid)
    is_open = t["status"] == "open"

    limit_str = f"{players_count}/{t['max_players']}" if t["max_players"] else f"{players_count}"
    status_str = "🟢 Регистрация открыта" if is_open else "🔴 Регистрация закрыта"

    text = (
        f"<b>🏅 {t['name']}</b>\n\n"
        f"{t['description'] or '—'}\n\n"
        f"📅 <b>Когда:</b> {t['start_at']}\n"
        f"👥 <b>Участников:</b> {limit_str}\n"
        f"📌 <b>Статус:</b> {status_str}\n"
    )
    if registered:
        text += "\n✅ <b>Ты зарегистрирован.</b>"

    await event.message.edit_text(
        text,
        reply_markup=tournament_detail_kb(tid, registered, is_open)
    )
    await event.answer()


# ---------- Регистрация ----------
@dp.callback_query(F.data.startswith("reg:"))
async def cb_register(event: CallbackQuery):
    tid = int(event.data.split(":", 1)[1])
    result = await register_for_tournament(tid, event.from_user.id)

    messages = {
        "ok": "✅ Ты успешно зарегистрирован на турнир!",
        "already": "⚠️ Ты уже зарегистрирован на этот турнир.",
        "full": "❌ Все места заняты.",
        "closed": "❌ Регистрация на этот турнир закрыта.",
        "not_found": "❌ Турнир не найден.",
    }
    await event.answer(messages.get(result, "Ошибка."), show_alert=True)

    t = await get_tournament(tid)
    if t:
        registered = await is_registered(tid, event.from_user.id)
        players_count = await count_registrations(tid)
        is_open = t["status"] == "open"
        limit_str = f"{players_count}/{t['max_players']}" if t["max_players"] else f"{players_count}"
        status_str = "🟢 Регистрация открыта" if is_open else "🔴 Регистрация закрыта"
        text = (
            f"<b>🏅 {t['name']}</b>\n\n"
            f"{t['description'] or '—'}\n\n"
            f"📅 <b>Когда:</b> {t['start_at']}\n"
            f"👥 <b>Участников:</b> {limit_str}\n"
            f"📌 <b>Статус:</b> {status_str}\n"
        )
        if registered:
            text += "\n✅ <b>Ты зарегистрирован.</b>"
        await event.message.edit_text(
            text,
            reply_markup=tournament_detail_kb(tid, registered, is_open)
        )

    if result == "ok":
        target = SUPPORT_CHAT_ID or ADMIN_ID
        if target:
            try:
                await bot.send_message(
                    target,
                    f"📥 <b>Новая регистрация</b>\n"
                    f"Игрок: {event.from_user.first_name} "
                    f"(@{event.from_user.username or 'без ника'}, ID {event.from_user.id})\n"
                    f"Турнир: {t['name']}"
                )
            except Exception as e:
                logging.error(f"Не удалось уведомить админа: {e}")


# ---------- Отмена регистрации ----------
@dp.callback_query(F.data.startswith("unreg:"))
async def cb_unregister(event: CallbackQuery):
    tid = int(event.data.split(":", 1)[1])
    result = await unregister_from_tournament(tid, event.from_user.id)

    if result == "ok":
        await event.answer("❌ Регистрация отменена.", show_alert=True)
    else:
        await event.answer("Ты не был зарегистрирован.", show_alert=True)
        return

    t = await get_tournament(tid)
    if t:
        players_count = await count_registrations(tid)
        is_open = t["status"] == "open"
        limit_str = f"{players_count}/{t['max_players']}" if t["max_players"] else f"{players_count}"
        status_str = "🟢 Регистрация открыта" if is_open else "🔴 Регистрация закрыта"
        text = (
            f"<b>🏅 {t['name']}</b>\n\n"
            f"{t['description'] or '—'}\n\n"
            f"📅 <b>Когда:</b> {t['start_at']}\n"
            f"👥 <b>Участников:</b> {limit_str}\n"
            f"📌 <b>Статус:</b> {status_str}\n"
        )
        await event.message.edit_text(
            text,
            reply_markup=tournament_detail_kb(tid, False, is_open)
        )


# ---------- Правила ----------
@dp.callback_query(F.data == "menu:rules")
@dp.message(Command("rules"))
async def show_rules(event):
    text = (
        "<b>📜 Правила платформы</b>\n\n"
        "1. Уважай других игроков.\n"
        "2. Запрещены читы, баги, договорняки.\n"
        "3. Опоздание на турнир > 5 минут = дисквалификация.\n"
        "4. Спорные ситуации решает администратор.\n"
        "5. Полный свод правил: <a href='https://example.com/rules'>по ссылке</a>."
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(
            text,
            reply_markup=main_menu_kb(),
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        await event.answer()
    else:
        await event.answer(
            text,
            reply_markup=main_menu_kb(),
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )


# ---------- Техподдержка: начало ----------
@dp.callback_query(F.data == "menu:support")
@dp.message(Command("support"))
async def support_start(event):
    text = (
        "<b>🛠 Техподдержка</b>\n\n"
        "Напиши одним сообщением суть проблемы — "
        "мы ответим в течение 24 часов.\n\n"
        "Чтобы отменить — нажми /cancel."
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text)
        await event.answer()
    else:
        await event.answer(text)
    SUPPORT_USERS.add(event.from_user.id)


# ---------- Отмена ----------
@dp.message(Command("cancel"))
async def cancel_support(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in SUPPORT_USERS:
        SUPPORT_USERS.remove(message.from_user.id)
    await message.answer("✅ Действие отменено.", reply_markup=main_menu_kb())


# =========================================================
# 🛠 АДМИН-ПАНЕЛЬ: СОЗДАНИЕ ТУРНИРА
# =========================================================

@dp.callback_query(F.data == "admin:create_tournament")
async def start_create_tournament(event: CallbackQuery, state: FSMContext):
    if event.from_user.id != ADMIN_ID:
        await event.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    
    await state.set_state(TournamentCreation.waiting_for_name)
    await event.message.edit_text(
        "<b>🛠 Создание нового турнира</b>\n\n"
        "Шаг 1/4: Введите <b>название</b> турнира.\n"
        "(Для отмены напишите /cancel)"
    )
    await event.answer()


@dp.message(TournamentCreation.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(TournamentCreation.waiting_for_description)
    await message.answer(
        "Шаг 2/4: Введите <b>описание</b> турнира.\n"
        "(Можно написать 'Нет' или оставить краткую информацию)"
    )


@dp.message(TournamentCreation.waiting_for_description)
async def process_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(TournamentCreation.waiting_for_start_at)
    await message.answer(
        "Шаг 3/4: Введите <b>дату и время</b> начала (например: 'Пятница, 20:00 МСК' или '25 декабря')."
    )


@dp.message(TournamentCreation.waiting_for_start_at)
async def process_start_at(message: Message, state: FSMContext):
    await state.update_data(start_at=message.text)
    await state.set_state(TournamentCreation.waiting_for_max_players)
    await message.answer(
        "Шаг 4/4: Введите <b>максимальное количество участников</b> (только число, например: 32).\n"
        "Если ограничений нет, напишите: 0"
    )


@dp.message(TournamentCreation.waiting_for_max_players)
async def process_max_players(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Пожалуйста, введите корректное число (например: 32 или 0).")
        return

    max_players = int(message.text)
    data = await state.get_data()
    
    await add_tournament(
        name=data["name"],
        description=data["description"],
        start_at=data["start_at"],
        max_players=max_players
    )
    
    await state.clear()
    await message.answer(
        f"✅ <b>Турнир успешно создан!</b>\n\n"
        f"🏅 <b>{data['name']}</b>\n"
        f"👥 Мест: {max_players if max_players > 0 else 'Без ограничений'}\n\n"
        "Теперь он доступен игрокам в разделе «Турниры».",
        reply_markup=main_menu_kb()
    )


# =========================================================
# 💬 АДМИН-ПАНЕЛЬ: ОТВЕТ НА ОБРАЩЕНИЕ В ПОДДЕРЖКУ
# =========================================================

@dp.callback_query(F.data.startswith("admin_reply_to:"))
async def admin_reply_start(event: CallbackQuery, state: FSMContext):
    """Админ нажимает кнопку 'Ответить' под обращением в поддержку."""
    if event.from_user.id != ADMIN_ID:
        await event.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    
    user_id = int(event.data.split(":")[1])
    await state.update_data(reply_to_user_id=user_id)
    await state.set_state(AdminReplyState.waiting_for_answer)
    
    await event.message.edit_text(
        f"{event.message.text}\n\n"
        f"<i>✍️ Напишите ответ пользователю (ID: {user_id}).\n"
        f"Для отмены напишите /cancel</i>"
    )
    await event.answer()


@dp.message(AdminReplyState.waiting_for_answer)
async def admin_reply_process(message: Message, state: FSMContext):
    """Админ пишет текст ответа."""
    data = await state.get_data()
    user_id = data["reply_to_user_id"]
    
    admin_name = message.from_user.first_name
    reply_text = f"💬 <b>Ответ от поддержки ({admin_name}):</b>\n\n{message.text}"
    
    try:
        await bot.send_message(user_id, reply_text)
        await message.answer(
            f"✅ Ответ отправлен пользователю (ID: {user_id})!",
            reply_markup=main_menu_kb()
        )
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить ответ: {e}")
    
    await state.clear()


# =========================================================
# 🆘 ТЕХПОДДЕРЖКА: ПОЛУЧЕНИЕ СООБЩЕНИЙ ОТ ПОЛЬЗОВАТЕЛЕЙ
# =========================================================

@dp.message(F.text, StateFilter(None))
async def support_receive(message: Message):
    """Пользователь пишет в техподдержку."""
    if message.from_user.id not in SUPPORT_USERS:
        return
    SUPPORT_USERS.remove(message.from_user.id)

    await create_ticket(message.from_user.id, message.text)
    await message.answer("✅ Твоё обращение принято! Мы скоро ответим.", reply_markup=main_menu_kb())

    target = SUPPORT_CHAT_ID or ADMIN_ID
    if target:
        try:
            # Создаём клавиатуру с кнопкой "Ответить"
            kb = InlineKeyboardBuilder()
            kb.button(
                text="💬 Ответить",
                callback_data=f"admin_reply_to:{message.from_user.id}"
            )
            
            await bot.send_message(
                target,
                f"🆘 <b>Обращение в ТП</b>\n"
                f"От: {message.from_user.first_name} "
                f"(@{message.from_user.username or 'без ника'}, ID {message.from_user.id})\n\n"
                f"{message.text}",
                reply_markup=kb.as_markup()
            )
        except Exception as e:
            logging.error(f"Не удалось отправить в ТП: {e}")


# ---------- Запуск ----------
async def on_startup():
    await init_db()


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())