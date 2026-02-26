from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, LabeledPrice, Message, PreCheckoutQuery

from bot.config import Settings
from bot.db.repository import BalanceError, CasinoRepository
from bot.games.logic import (
    ResolvedBet,
    create_mines_state,
    effective_multiplier_with_edge,
    generate_crash_point,
    mines_cashout,
    next_crash_multiplier,
    open_mines_cell,
    payout_with_edge,
    resolve_dice_duel,
    resolve_emoji_game,
    resolve_dice,
    resolve_roulette,
    spin_slots,
)
from bot.handlers.states import BetFlow
from bot.keyboards import (
    admin_panel_keyboard,
    admin_withdrawals_keyboard,
    admin_withdraw_request_alert_keyboard,
    admin_users_keyboard,
    back_keyboard,
    crash_cashout_keyboard,
    deposit_crypto_amount_keyboard,
    deposit_crypto_asset_keyboard,
    deposit_method_keyboard,
    dice_choice_keyboard,
    emoji_game_choice_keyboard,
    games_keyboard,
    invoice_keyboard,
    main_menu_inline_keyboard,
    main_menu_keyboard,
    MENU_BALANCE,
    MENU_DEPOSIT,
    MENU_PLAY,
    MENU_PROFILE,
    MENU_REF,
    MENU_SUPPORT,
    MENU_WITHDRAW,
    mines_count_keyboard,
    mines_grid_keyboard,
    profile_actions_keyboard,
    replay_keyboard,
    roulette_keyboard,
    stake_amount_keyboard,
    stars_amount_keyboard,
)
from bot.services.cryptobot import CryptoBotClient, CryptoBotError
from bot.utils import fmt_money, q_money, to_decimal

router = Router(name="casino")
LOGGER = logging.getLogger(__name__)

DICE_EMOJI = "\U0001F3B2"
EMOJI_BY_GAME = {
    "football": "\u26BD",
    "basketball": "\U0001F3C0",
    "darts": "\U0001F3AF",
    "bowling": "\U0001F3B3",
}
GIVE_ALLOWED_USERNAMES = {"fermiy100"}
ADMIN_ALLOWED_USERNAMES = {"fermiy100"}
CRASH_TASKS: dict[str, asyncio.Task] = {}
MISSING_BANNERS_WARNED: set[str] = set()
WELCOME_BONUS = Decimal("0.10")
CRASH_TICK_SEC = 0.45
CRASH_START_MULTIPLIER = Decimal("1.01")
ADMIN_USERS_PAGE_SIZE = 20
ADMIN_WITHDRAWALS_PAGE_SIZE = 10
TME_MESSAGE_LINK_RE = re.compile(
    r"^(?:https?://)?t\.me/(?:(?:c/(?P<internal_id>\d+))|(?P<username>[A-Za-z0-9_]+))/(?P<message_id>\d+)(?:\?.*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TelegramMessageSource:
    from_chat_id: int | str
    message_id: int


VISIBLE_GAMES = {
    "slots",
    "dice",
    "football",
    "basketball",
    "crash",
    "roulette",
    "mines",
}

GAME_LABELS = {
    "slots": "🎰 Слоты",
    "dice": "🎲 Кости",
    "football": "⚽ Футбол",
    "basketball": "🏀 Баскетбол",
    "darts": "🎯 Дартс",
    "bowling": "🎳 Боулинг",
    "crash": "🚀 Crash",
    "roulette": "💀 Русская рулетка",
    "mines": "💣 Мины",
}

PLAY_TEXTS = {"Играть", MENU_PLAY}
BALANCE_TEXTS = {"Баланс", MENU_BALANCE}
PROFILE_TEXTS = {"Профиль", MENU_PROFILE}
REF_TEXTS = {"Реф. программа", MENU_REF}
DEPOSIT_TEXTS = {"Пополнить", MENU_DEPOSIT}
WITHDRAW_TEXTS = {"Вывести", MENU_WITHDRAW}
SUPPORT_TEXTS = {"Техподдержка", MENU_SUPPORT}


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return

    payload = None
    if message.text and " " in message.text:
        payload = message.text.split(" ", maxsplit=1)[1].strip()

    await repo.ensure_user(message.from_user, payload)
    welcome_added = await repo.grant_welcome_bonus_once(
        user_id=message.from_user.id,
        amount=WELCOME_BONUS,
    )
    await state.clear()
    bonus_line = (
        f"\n🎁 Стартовый бонус: <b>{fmt_money(WELCOME_BONUS)}</b> уже на балансе."
        if welcome_added
        else ""
    )
    await _send_main_menu_banner(
        message=message,
        settings=settings,
        caption=(
            "🏠 <b>Главное меню</b>\n"
            "Выберите действие кнопками ниже.\n"
            "🎲 Чтобы начать игру, нажмите <b>Играть</b>."
            f"{bonus_line}"
        ),
        reply_markup=main_menu_inline_keyboard(),
    )
    await _send_persistent_menu_hint(message)

@router.message(Command("games"))
@router.message(Command("bet"))
@router.message(F.text.in_(PLAY_TEXTS))
async def open_games(message: Message, repo: CasinoRepository, settings: Settings) -> None:
    if message.from_user:
        await repo.ensure_user(message.from_user)
    await _send_main_menu_banner(
        message=message,
        settings=settings,
        caption=(
            "🕹️ <b>Выберите игру</b>\n"
            "После выбора предложу быстрые суммы ставки."
        ),
        reply_markup=games_keyboard(),
    )


@router.message(Command("balance"))
@router.message(F.text.in_(BALANCE_TEXTS))
async def show_balance(message: Message, repo: CasinoRepository) -> None:
    if not message.from_user:
        return
    await repo.ensure_user(message.from_user)
    profile = await repo.get_profile(message.from_user.id)
    await message.answer(
        f"💰 <b>Ваш баланс</b>\n"
        f"Доступно: <b>{fmt_money(profile.balance)}</b>",
        reply_markup=back_keyboard(),
    )


@router.message(Command("profile"))
@router.message(F.text.in_(PROFILE_TEXTS))
async def show_profile(
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return

    await repo.ensure_user(message.from_user)
    profile = await repo.get_profile(message.from_user.id)

    display_name = html.escape(
        str(profile.first_name or profile.username or profile.user_id)
    )

    text = (
        f"👤 <b>Профиль</b>\n"
        f"Игрок: <b>{display_name}</b>\n"
        f"ID: <code>{profile.user_id}</code>\n"
        f"Баланс: <b>{fmt_money(profile.balance)}</b>\n\n"
        f"🎲 <b>Статистика ставок</b>\n"
        f"• Всего ставок: {profile.total_bets}\n"
        f"• Общая сумма ставок: {fmt_money(profile.total_wager)}\n\n"
        f"🎁 <b>Реферальная программа</b>\n"
        f"• Приглашено: {profile.invited_count}\n"
        f"• Заработано: {fmt_money(profile.referral_earnings)}"
    )

    await message.answer(
        text,
        reply_markup=profile_actions_keyboard(
            is_admin=await _is_admin_access(
                user_id=message.from_user.id,
                username=message.from_user.username,
                settings=settings,
                repo=repo,
            ),
        ),
    )


@router.message(Command("ref"))
@router.message(F.text.in_(REF_TEXTS))
async def show_referral(message: Message, repo: CasinoRepository, settings: Settings) -> None:
    if not message.from_user:
        return

    await repo.ensure_user(message.from_user)
    profile = await repo.get_profile(message.from_user.id)
    ref_link = await repo.get_ref_link(message.from_user.id, settings.bot_username)

    text = (
        "🎁 <b>Реферальная программа</b>\n\n"
        "Приглашайте игроков и получайте <b>10%</b> от их проигрышей.\n\n"
        f"🔗 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"📊 Статистика:\n"
        f"• Приглашено: {profile.invited_count}\n"
        f"• Доход: {fmt_money(profile.referral_earnings)}"
    )
    await message.answer(text, reply_markup=back_keyboard())


@router.message(Command("support"))
@router.message(F.text.in_(SUPPORT_TEXTS))
async def show_support(message: Message, settings: Settings) -> None:
    support_username = settings.support_username.strip().replace("@", "")
    if support_username:
        await message.answer(
            "🛟 <b>Техподдержка</b>\n"
            f"По любым вопросам обращайтесь к @{html.escape(support_username)}",
            reply_markup=back_keyboard(),
        )
    else:
        await message.answer(
            "🛟 <b>Техподдержка</b>\n"
            "Контакт не указан администратором.",
            reply_markup=back_keyboard(),
        )


@router.callback_query(F.data == "menu_profile")
async def on_menu_profile(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        return

    await callback.answer()
    await repo.ensure_user(callback.from_user)
    profile = await repo.get_profile(callback.from_user.id)

    display_name = html.escape(
        str(profile.first_name or profile.username or profile.user_id)
    )
    text = (
        f"👤 <b>Профиль</b>\n"
        f"Игрок: <b>{display_name}</b>\n"
        f"ID: <code>{profile.user_id}</code>\n"
        f"Баланс: <b>{fmt_money(profile.balance)}</b>\n\n"
        f"🎲 <b>Статистика ставок</b>\n"
        f"• Всего ставок: {profile.total_bets}\n"
        f"• Общая сумма ставок: {fmt_money(profile.total_wager)}\n\n"
        f"🎁 <b>Реферальная программа</b>\n"
        f"• Приглашено: {profile.invited_count}\n"
        f"• Заработано: {fmt_money(profile.referral_earnings)}"
    )
    await _edit_or_send(
        callback.message,
        text,
        reply_markup=profile_actions_keyboard(
            is_admin=await _is_admin_access(
                user_id=callback.from_user.id,
                username=callback.from_user.username,
                settings=settings,
                repo=repo,
            ),
        ),
    )


@router.callback_query(F.data == "menu_balance")
async def on_menu_balance(
    callback: CallbackQuery,
    repo: CasinoRepository,
) -> None:
    if not callback.from_user or not callback.message:
        return
    await callback.answer()
    await repo.ensure_user(callback.from_user)
    profile = await repo.get_profile(callback.from_user.id)
    await _edit_or_send(
        callback.message,
        f"💰 <b>Ваш баланс</b>\n"
        f"Доступно: <b>{fmt_money(profile.balance)}</b>",
        reply_markup=back_keyboard(),
    )


@router.callback_query(F.data == "menu_ref")
async def on_menu_ref(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        return
    await callback.answer()
    await repo.ensure_user(callback.from_user)
    profile = await repo.get_profile(callback.from_user.id)
    ref_link = await repo.get_ref_link(callback.from_user.id, settings.bot_username)
    await _edit_or_send(
        callback.message,
        "🎁 <b>Реферальная программа</b>\n\n"
        "Приглашайте игроков и получайте <b>10%</b> от их проигрышей.\n\n"
        f"🔗 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"📊 Статистика:\n"
        f"• Приглашено: {profile.invited_count}\n"
        f"• Доход: {fmt_money(profile.referral_earnings)}",
        reply_markup=back_keyboard(),
    )


@router.callback_query(F.data == "menu_support")
async def on_menu_support(callback: CallbackQuery, settings: Settings) -> None:
    if not callback.message:
        return
    await callback.answer()
    support_username = settings.support_username.strip().replace("@", "")
    if support_username:
        await _edit_or_send(
            callback.message,
            "🛟 <b>Техподдержка</b>\n"
            f"По любым вопросам обращайтесь к @{html.escape(support_username)}",
            reply_markup=back_keyboard(),
        )
    else:
        await _edit_or_send(
            callback.message,
            "🛟 <b>Техподдержка</b>\n"
            "Контакт не указан администратором.",
            reply_markup=back_keyboard(),
        )


@router.message(Command("give"))
async def cmd_give(message: Message, repo: CasinoRepository) -> None:
    if not message.from_user:
        return

    username = (message.from_user.username or "").lower().replace("@", "")
    if username not in GIVE_ALLOWED_USERNAMES:
        await message.answer("❌ Недостаточно прав для этой команды.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/give 100</code>")
        return

    amount = _parse_amount(parts[1])
    if amount is None:
        await message.answer("❌ Укажите корректную сумму. Пример: <code>/give 100</code>")
        return

    await repo.ensure_user(message.from_user)
    await repo.credit_balance(
        user_id=message.from_user.id,
        amount=amount,
        kind="admin_grant",
        external_id=f"give_{uuid.uuid4().hex[:10]}",
        description="Начисление через /give",
        details={"by_username": username},
    )
    profile = await repo.get_profile(message.from_user.id)
    await message.answer(
        f"✅ Начислено: <b>{fmt_money(amount)}</b>\n"
        f"Новый баланс: <b>{fmt_money(profile.balance)}</b>"
    )


@router.message(Command("admin"))
async def open_admin_panel(
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not message.from_user:
        return
    if not await _is_admin_access(
        user_id=message.from_user.id,
        username=message.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await message.answer("❌ Недостаточно прав.")
        return

    await repo.ensure_user(message.from_user)
    await state.clear()
    text = await _render_admin_panel_text(repo=repo, settings=settings)
    await message.answer(text, reply_markup=admin_panel_keyboard())


@router.callback_query(F.data == "admin:refresh")
async def on_admin_refresh(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    text = await _render_admin_panel_text(repo=repo, settings=settings)
    await callback.answer()
    await _edit_or_send(callback.message, text, reply_markup=admin_panel_keyboard())


@router.callback_query(F.data == "admin:stats")
async def on_admin_stats(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    text = await _render_admin_panel_text(repo=repo, settings=settings)
    await callback.answer()
    await _edit_or_send(callback.message, text, reply_markup=admin_panel_keyboard())


@router.callback_query(F.data.startswith("admin:users:"))
async def on_admin_users(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    try:
        offset = max(0, int(callback.data.split(":", maxsplit=2)[2]))
    except (IndexError, ValueError):
        offset = 0

    total = await repo.count_users()
    rows = await repo.list_users_balances(limit=ADMIN_USERS_PAGE_SIZE, offset=offset)
    if not rows:
        text = "👥 <b>Пользователи</b>\nНет данных."
    else:
        lines = []
        for idx, row in enumerate(rows, start=offset + 1):
            label = _user_label(row.username, row.first_name, row.user_id)
            lines.append(
                f"{idx}. {label} — <b>{fmt_money(row.balance)}</b> "
                f"(ставок: {row.total_bets})"
            )
        text = (
            f"👥 <b>Пользователи</b>\n"
            f"Всего: <b>{total}</b>\n"
            f"Показаны: <b>{offset + 1}-{offset + len(rows)}</b>\n\n"
            + "\n".join(lines)
        )

    await callback.answer()
    await _edit_or_send(
        callback.message,
        text,
        reply_markup=admin_users_keyboard(
            offset=offset,
            total=total,
            limit=ADMIN_USERS_PAGE_SIZE,
        ),
    )


@router.callback_query(F.data.startswith("admin:withdrawals:"))
async def on_admin_withdrawals(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    try:
        offset = max(0, int(callback.data.split(":", maxsplit=2)[2]))
    except (IndexError, ValueError):
        offset = 0

    total = await repo.count_pending_withdraw_requests()
    requests = await repo.list_pending_withdraw_requests(
        limit=ADMIN_WITHDRAWALS_PAGE_SIZE,
        offset=offset,
    )

    if not requests:
        text = "📤 <b>Заявки на вывод</b>\nАктивных заявок нет."
    else:
        lines: list[str] = []
        for idx, req in enumerate(requests, start=offset + 1):
            label = _user_label(req.username, req.first_name, req.user_id)
            created = req.created_at.strftime("%d.%m %H:%M")
            lines.append(
                f"{idx}. <code>#{req.request_id}</code> • {label}\n"
                f"Сумма: <b>{fmt_money(req.amount)}</b> • {created}"
            )
        text = (
            "📤 <b>Заявки на вывод (pending)</b>\n"
            f"Всего: <b>{total}</b>\n\n"
            + "\n\n".join(lines)
        )

    await callback.answer()
    await _edit_or_send(
        callback.message,
        text,
        reply_markup=admin_withdrawals_keyboard(
            request_ids=[req.request_id for req in requests],
            offset=offset,
            total=total,
            limit=ADMIN_WITHDRAWALS_PAGE_SIZE,
        ),
    )


@router.callback_query(F.data.startswith("admin:withdraw_confirm:"))
async def on_admin_withdraw_confirm(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    try:
        request_id = int(callback.data.split(":", maxsplit=2)[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный id заявки", show_alert=True)
        return

    try:
        approved = await repo.approve_withdraw_request(
            request_id=request_id,
            admin_id=callback.from_user.id,
        )
    except NotFoundError:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    except BalanceError:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    try:
        await callback.bot.send_message(
            chat_id=approved.user_id,
            text=(
                "✅ <b>Вывод подтвержден</b>\n"
                f"Заявка: <code>#{approved.request_id}</code>\n"
                f"Сумма: <b>{fmt_money(approved.amount)}</b>\n"
                "Ожидайте перевод от администратора."
            ),
        )
    except Exception:
        pass

    await callback.answer("Вывод подтвержден")
    # Обновляем список заявок после подтверждения.
    total = await repo.count_pending_withdraw_requests()
    requests = await repo.list_pending_withdraw_requests(
        limit=ADMIN_WITHDRAWALS_PAGE_SIZE,
        offset=0,
    )
    if not requests:
        text = "📤 <b>Заявки на вывод</b>\nАктивных заявок нет."
    else:
        lines: list[str] = []
        for idx, req in enumerate(requests, start=1):
            label = _user_label(req.username, req.first_name, req.user_id)
            created = req.created_at.strftime("%d.%m %H:%M")
            lines.append(
                f"{idx}. <code>#{req.request_id}</code> • {label}\n"
                f"Сумма: <b>{fmt_money(req.amount)}</b> • {created}"
            )
        text = (
            "📤 <b>Заявки на вывод (pending)</b>\n"
            f"Всего: <b>{total}</b>\n\n"
            + "\n\n".join(lines)
        )
    await _edit_or_send(
        callback.message,
        text,
        reply_markup=admin_withdrawals_keyboard(
            request_ids=[req.request_id for req in requests],
            offset=0,
            total=total,
            limit=ADMIN_WITHDRAWALS_PAGE_SIZE,
        ),
    )


@router.callback_query(F.data == "admin:set_stars_rate")
async def on_admin_set_stars_rate(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(BetFlow.waiting_admin_stars_rate)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "💱 Введите новый курс Stars в USD за 1⭐.\n"
            "Пример: <code>0.017</code>"
        )


@router.callback_query(F.data == "admin:set_bot_token")
async def on_admin_set_bot_token(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(BetFlow.waiting_admin_bot_token)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🔐 Отправьте новый <code>BOT_TOKEN</code>.\n"
            "Токен будет сохранен в .env, затем выполните перезапуск."
        )


@router.callback_query(F.data == "admin:broadcast")
async def on_admin_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(BetFlow.waiting_admin_broadcast)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📣 Отправьте текст рассылки.\n"
            "Сообщение будет разослано всем пользователям."
        )


@router.callback_query(F.data == "admin:add_admin")
async def on_admin_add_admin(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(BetFlow.waiting_admin_add_admin)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "👮 Введите ID пользователя или @username, которого нужно сделать админом.\n"
            "Пример: <code>123456789</code> или <code>@nickname</code>"
        )


@router.callback_query(F.data == "admin:grant")
async def on_admin_grant(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(BetFlow.waiting_admin_grant)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "💸 Введите: <code>user_id сумма</code> или <code>@username сумма</code>\n"
            "Пример: <code>123456789 50</code>"
        )


@router.callback_query(F.data == "admin:stop")
async def on_admin_stop(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return
    if not await _is_admin_access(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.answer("Остановка...")
    if callback.message:
        await callback.message.answer("🛑 Бот остановлен администратором.")
    loop = asyncio.get_running_loop()
    loop.call_later(0.7, os._exit, 0)


@router.message(BetFlow.waiting_admin_stars_rate)
async def on_admin_stars_rate_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    if not await _is_admin_access(
        user_id=message.from_user.id,
        username=message.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await state.clear()
        await message.answer("❌ Недостаточно прав.")
        return

    value = _parse_positive_decimal(message.text or "")
    if value is None:
        await message.answer("❌ Некорректный формат. Пример: <code>0.017</code>")
        return
    if value <= Decimal("0") or value > Decimal("10"):
        await message.answer("❌ Укажите значение в диапазоне (0; 10].")
        return

    rate = value.quantize(Decimal("0.0001"))
    await repo.set_app_setting(key="stars_usd_rate", value=str(rate))
    await state.clear()
    await message.answer(
        f"✅ Новый курс сохранен: <b>1⭐ = ${rate}</b>\n"
        "Изменение применяется сразу.",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(BetFlow.waiting_admin_bot_token)
async def on_admin_bot_token_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    if not await _is_admin_access(
        user_id=message.from_user.id,
        username=message.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await state.clear()
        await message.answer("❌ Недостаточно прав.")
        return

    token = (message.text or "").strip()
    if not _looks_like_bot_token(token):
        await message.answer("❌ Неверный формат токена.")
        return

    try:
        _set_env_value("BOT_TOKEN", token)
        await repo.set_app_setting(key="bot_token_updated_at", value=str(uuid.uuid4()))
    except OSError as err:
        await message.answer(f"❌ Не удалось сохранить токен: {html.escape(str(err))}")
        return

    await state.clear()
    await message.answer(
        "✅ Новый BOT_TOKEN сохранен в .env.\n"
        "Перезапустите бота, чтобы применить токен.",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(BetFlow.waiting_admin_broadcast)
async def on_admin_broadcast_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    if not await _is_admin_access(
        user_id=message.from_user.id,
        username=message.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await state.clear()
        await message.answer("❌ Недостаточно прав.")
        return

    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("❌ Сообщение слишком короткое.")
        return

    user_ids = await repo.get_all_user_ids()
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await message.bot.send_message(chat_id=user_id, text=text, parse_mode=None)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.03)

    await state.clear()
    await message.answer(
        f"📣 Рассылка завершена\n"
        f"Отправлено: <b>{sent}</b>\n"
        f"Ошибок: <b>{failed}</b>",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(BetFlow.waiting_admin_add_admin)
async def on_admin_add_admin_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    if not await _is_admin_access(
        user_id=message.from_user.id,
        username=message.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await state.clear()
        await message.answer("❌ Недостаточно прав.")
        return

    target = (message.text or "").strip()
    target_user_id = await _resolve_user_id(repo, target)
    if target_user_id is None:
        await message.answer("❌ Пользователь не найден. Укажите корректный ID или @username.")
        return

    dynamic_admin_ids = await _get_dynamic_admin_ids(repo)
    dynamic_admin_ids.add(target_user_id)
    await _save_dynamic_admin_ids(repo, dynamic_admin_ids)

    await state.clear()
    await message.answer(
        f"✅ Админ добавлен: <code>{target_user_id}</code>",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(BetFlow.waiting_admin_grant)
async def on_admin_grant_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    if not await _is_admin_access(
        user_id=message.from_user.id,
        username=message.from_user.username,
        settings=settings,
        repo=repo,
    ):
        await state.clear()
        await message.answer("❌ Недостаточно прав.")
        return

    parts = (message.text or "").strip().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>user_id сумма</code> или <code>@username сумма</code>")
        return

    target_raw, amount_raw = parts
    target_user_id = await _resolve_user_id(repo, target_raw)
    if target_user_id is None:
        await message.answer("❌ Пользователь не найден.")
        return

    amount = _parse_amount(amount_raw)
    if amount is None or amount <= 0:
        await message.answer("❌ Некорректная сумма.")
        return

    await repo.credit_balance(
        user_id=target_user_id,
        amount=amount,
        kind="admin_grant",
        external_id=f"admin_grant_{uuid.uuid4().hex[:10]}",
        description="Выдача через админ-панель",
        details={"by_admin_id": message.from_user.id},
    )
    profile = await repo.get_profile(target_user_id)
    await state.clear()

    await message.answer(
        f"✅ Начислено {fmt_money(amount)} пользователю <code>{target_user_id}</code>.\n"
        f"Новый баланс: <b>{fmt_money(profile.balance)}</b>",
        reply_markup=admin_panel_keyboard(),
    )
    try:
        await message.bot.send_message(
            chat_id=target_user_id,
            text=(
                "💸 <b>Начисление от администратора</b>\n"
                f"Сумма: <b>{fmt_money(amount)}</b>\n"
                f"Новый баланс: <b>{fmt_money(profile.balance)}</b>"
            ),
        )
    except Exception:
        pass


@router.callback_query(F.data == "show_games")
async def on_show_games(callback: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await _edit_or_send(
            callback.message,
            "🎮 <b>Выберите игру</b>\n"
            "После выбора предложу быстрые суммы ставки.",
            reply_markup=games_keyboard(),
        )

@router.callback_query(F.data == "open_main_menu")
async def on_open_main_menu(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
) -> None:
    await callback.answer("Главное меню")
    await state.clear()
    if callback.message:
        await _edit_or_send(
            callback.message,
            "🏠 <b>Главное меню</b>\n"
            "Выберите действие кнопками ниже.\n"
            "🎲 Чтобы начать игру, нажмите <b>Играть</b>.",
            reply_markup=main_menu_inline_keyboard(),
        )


@router.callback_query(F.data.startswith("select_game:"))
async def on_select_game(callback: CallbackQuery, state: FSMContext) -> None:
    game = callback.data.split(":", maxsplit=1)[1]

    if game not in GAME_LABELS or game not in VISIBLE_GAMES:
        await callback.answer("Игра не найдена", show_alert=True)
        return

    current_data = await state.get_data()
    if current_data.get("game") == game and not current_data.get("stake"):
        await callback.answer("Игра уже выбрана")
        return

    await state.set_state(BetFlow.waiting_stake)
    await state.set_data({"game": game})
    await callback.answer(f"Выбрано: {GAME_LABELS[game]}")
    await _edit_or_send(
        callback.message,
        f"✅ <b>Игра выбрана:</b> {GAME_LABELS[game]}\n\n"
        "💵 Выберите сумму ставки кнопкой ниже\n"
        "или введите ее вручную сообщением.",
        reply_markup=stake_amount_keyboard(),
    )
 


@router.callback_query(F.data == "stake_manual")
async def on_stake_manual(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await _edit_or_send(
        callback.message,
        "✍️ Введите сумму ставки числом.\nПример: <code>25</code>",
        reply_markup=stake_amount_keyboard(),
    )


@router.callback_query(F.data.startswith("stake_preset:"))
async def on_stake_preset(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    stake = _parse_amount(callback.data.split(":", maxsplit=1)[1])
    if stake is None:
        await callback.answer("Некорректная сумма", show_alert=True)
        return

    if stake < q_money(settings.min_bet) or stake > q_money(settings.max_bet):
        await callback.answer(
            f"Ставка вне диапазона {fmt_money(settings.min_bet)}..{fmt_money(settings.max_bet)}",
            show_alert=True,
        )
        return

    await callback.answer(f"Ставка: {fmt_money(stake)}")
    if callback.message is None:
        return
    await _apply_stake_selection(
        message=callback.message,
        state=state,
        repo=repo,
        settings=settings,
        user_id=callback.from_user.id,
        stake=stake,
    )


@router.message(BetFlow.waiting_stake)
async def on_stake_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return

    stake = _parse_amount(message.text or "")
    if stake is None:
        await message.answer("❌ Введите корректную сумму, например: <code>10</code>")
        return

    if stake < q_money(settings.min_bet) or stake > q_money(settings.max_bet):
        await message.answer(
            f"❌ Ставка должна быть от {fmt_money(settings.min_bet)} до {fmt_money(settings.max_bet)}"
        )
        return

    await _apply_stake_selection(
        message=message,
        state=state,
        repo=repo,
        settings=settings,
        user_id=message.from_user.id,
        stake=stake,
    )


@router.callback_query(F.data.startswith("dice_choice:"))
async def on_dice_choice(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    data = await state.get_data()
    stake = _stake_from_state(data)
    game = data.get("game")
    if not stake or game != "dice":
        await callback.answer("Сначала выберите игру и ставку", show_alert=True)
        return

    choice = callback.data.split(":", maxsplit=1)[1]

    try:
        bet = await repo.place_bet(
            user_id=callback.from_user.id,
            game="dice",
            stake=stake,
            details={"choice": choice},
        )
    except BalanceError:
        await callback.message.answer("❌ Недостаточно средств на балансе")
        await state.clear()
        return

    await _post_bet_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game="dice",
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Исход: <b>{html.escape(_dice_choice_title(choice))}</b>"],
    )

    await callback.answer("Ставка принята")
    if choice == "duel":
        player_dice = await callback.message.answer_dice(emoji=DICE_EMOJI)
        await asyncio.sleep(0.35)
        bot_dice = await callback.message.answer_dice(emoji=DICE_EMOJI)
        player_value = player_dice.dice.value if player_dice.dice else 1
        bot_value = bot_dice.dice.value if bot_dice.dice else 1
        outcome = resolve_dice_duel(
            stake=stake,
            edge=settings.house_edge_dice,
            player_value=player_value,
            bot_value=bot_value,
        )
    else:
        dice_msg = await callback.message.answer_dice(emoji=DICE_EMOJI)
        result_value = dice_msg.dice.value if dice_msg.dice else 1
        outcome = resolve_dice(stake, settings.house_edge_dice, result_value, choice)

    status = "won" if outcome.won else "lost"
    if outcome.details.get("duel_result") == "draw":
        status = "push"

    await repo.finalize_bet(
        bet_id=bet.id,
        status=status,
        payout=outcome.payout,
        base_multiplier=outcome.base_multiplier,
        applied_edge=outcome.applied_edge,
        details=outcome.details,
    )
    await _post_result_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game="dice",
        stake=stake,
        payout=outcome.payout,
        status=status,
        bet_id=bet.id,
        extra_lines=_channel_dice_result_lines(outcome),
    )
    text = _format_dice_result_text(stake=stake, outcome=outcome)
    replay_markup = replay_keyboard(game="dice", stake=stake, choice=choice)
    if status in {"won", "lost"}:
        await _send_outcome_banner(
            message=callback.message,
            settings=settings,
            won=status == "won",
            caption=text,
            reply_markup=replay_markup,
        )
    else:
        await callback.message.answer(text, reply_markup=replay_markup)
    await state.clear()


@router.callback_query(F.data.startswith("emoji_choice:"))
async def on_emoji_choice(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    parts = callback.data.split(":", maxsplit=2)
    if len(parts) != 3:
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    _, game_name, choice = parts
    if game_name not in EMOJI_BY_GAME:
        await callback.answer("Игра не найдена", show_alert=True)
        return

    data = await state.get_data()
    stake = _stake_from_state(data)
    game = data.get("game")
    if not stake or game != game_name:
        await callback.answer("Сначала выберите игру и ставку", show_alert=True)
        return

    try:
        bet = await repo.place_bet(
            user_id=callback.from_user.id,
            game=game_name,
            stake=stake,
            details={"choice": choice},
        )
    except BalanceError:
        await callback.message.answer("❌ Недостаточно средств на балансе")
        await state.clear()
        return

    await _post_bet_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game=game_name,
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Исход: <b>{html.escape(_emoji_choice_title(game_name, choice))}</b>"],
    )

    await callback.answer("Ставка принята")
    emoji_msg = await callback.message.answer_dice(emoji=EMOJI_BY_GAME[game_name])
    result_value = emoji_msg.dice.value if emoji_msg.dice else 1

    outcome = resolve_emoji_game(
        stake=stake,
        edge=settings.house_edge_dice,
        game=game_name,
        dice_value=result_value,
        choice=choice,
    )

    await repo.finalize_bet(
        bet_id=bet.id,
        status="won" if outcome.won else "lost",
        payout=outcome.payout,
        base_multiplier=outcome.base_multiplier,
        applied_edge=outcome.applied_edge,
        details=outcome.details,
    )
    await _post_result_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game=game_name,
        stake=stake,
        payout=outcome.payout,
        status="won" if outcome.won else "lost",
        bet_id=bet.id,
        extra_lines=_channel_emoji_result_lines(outcome),
    )
    text = _format_emoji_result_text(game_name=game_name, stake=stake, outcome=outcome)
    await _send_outcome_banner(
        message=callback.message,
        settings=settings,
        won=outcome.won,
        caption=text,
        reply_markup=replay_keyboard(game=game_name, stake=stake, choice=choice),
    )
    await state.clear()


@router.callback_query(F.data.startswith("crash_cashout:"))
async def on_crash_cashout(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    session_id = callback.data.split(":", maxsplit=1)[1]
    game_session = await repo.get_game_session(session_id)
    if not game_session or game_session.status != "active":
        await callback.answer("Раунд уже завершен", show_alert=True)
        return

    if game_session.user_id != callback.from_user.id:
        await callback.answer("Это не ваш раунд", show_alert=True)
        return

    state_data = game_session.state
    stake = to_decimal(state_data.get("stake", "0"))
    current_multiplier = to_decimal(state_data.get("current_multiplier", "1.00"))
    crash_point = to_decimal(state_data.get("crash_point", "1.00"))
    effective_current = effective_multiplier_with_edge(
        current_multiplier, settings.house_edge_crash
    )
    effective_crash_point = effective_multiplier_with_edge(
        crash_point, settings.house_edge_crash
    )
    if current_multiplier >= crash_point:
        await repo.finalize_bet(
            bet_id=game_session.bet_id,
            status="lost",
            payout=Decimal("0"),
            base_multiplier=Decimal("0"),
            applied_edge=Decimal(str(settings.house_edge_crash)),
            details={
                "manual_cashout": False,
                "crash_point": str(crash_point),
                "late_cashout_click": True,
            },
        )
        await _post_result_to_channel(
            bot=callback.bot,
            settings=settings,
            repo=repo,
            user_id=callback.from_user.id,
            game="crash",
            stake=stake,
            payout=Decimal("0"),
            status="lost",
            bet_id=game_session.bet_id,
            extra_lines=[f"Точка взрыва: <b>x{effective_crash_point}</b>"],
        )
        await _send_outcome_banner(
            message=callback.message,
            settings=settings,
            won=False,
            caption=(
                "💥 <b>Crash завершен</b>\n"
                f"Ставка: <b>{fmt_money(stake)}</b>\n"
                f"Взрыв на: <b>x{effective_crash_point}</b>\n"
                f"❌ <b>Проигрыш: {fmt_money(stake)}</b>"
            ),
            reply_markup=replay_keyboard(game="crash", stake=stake),
        )

        state_data["final_multiplier"] = str(current_multiplier)
        state_data["final_effective_multiplier"] = str(effective_current)
        state_data["phase"] = "finished"
        state_data["result"] = "crashed"
        await repo.update_game_session(
            session_id=game_session.id,
            state=state_data,
            status="crashed",
        )

        _cancel_crash_task(session_id)
        await callback.answer("Слишком поздно: ракета уже взорвалась", show_alert=True)
        await _edit_or_send(
            callback.message,
            "",
        )
        return

    payout = payout_with_edge(stake, current_multiplier, settings.house_edge_crash)
    await repo.finalize_bet(
        bet_id=game_session.bet_id,
        status="won",
        payout=payout,
        base_multiplier=current_multiplier,
        applied_edge=Decimal(str(settings.house_edge_crash)),
        details={
            "manual_cashout": True,
            "current_multiplier": str(current_multiplier),
            "effective_multiplier": str(effective_current),
            "crash_point": str(crash_point),
        },
    )
    await _post_result_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game="crash",
        stake=stake,
        payout=payout,
        status="won",
        bet_id=game_session.bet_id,
        extra_lines=[
            f"Кэшаут на: <b>x{effective_current}</b>",
            f"Точка взрыва: <b>x{effective_crash_point}</b>",
        ],
    )
    result_text = (
        "🚀 <b>Crash завершен</b>\n"
        f"Ставка: <b>{fmt_money(stake)}</b>\n"
        f"Забрали на: <b>x{effective_current}</b>\n"
        f"Выплата: <b>{fmt_money(payout)}</b>\n"
        f"Точка взрыва: <b>x{effective_crash_point}</b>"
    )
    await _send_outcome_banner(
        message=callback.message,
        settings=settings,
        won=True,
        caption=result_text,
        reply_markup=replay_keyboard(game="crash", stake=stake),
    )

    state_data["final_multiplier"] = str(current_multiplier)
    state_data["final_effective_multiplier"] = str(effective_current)
    state_data["phase"] = "finished"
    state_data["result"] = "cashed_out"
    await repo.update_game_session(
        session_id=game_session.id,
        state=state_data,
        status="cashed_out",
    )

    _cancel_crash_task(session_id)
    await callback.answer("Кэшаут выполнен")
    await _edit_or_send(
        callback.message,
        "",
    )


@router.callback_query(F.data.startswith("roulette_choice:"))
async def on_roulette_choice(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    data = await state.get_data()
    stake = _stake_from_state(data)
    game = data.get("game")
    if not stake or game != "roulette":
        await callback.answer("Сначала выберите игру и ставку", show_alert=True)
        return

    chamber = int(callback.data.split(":", maxsplit=1)[1])

    try:
        bet = await repo.place_bet(
            user_id=callback.from_user.id,
            game="roulette",
            stake=stake,
            details={"chamber": chamber},
        )
    except BalanceError:
        await callback.message.answer("❌ Недостаточно средств на балансе")
        await state.clear()
        return

    await _post_bet_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game="roulette",
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Слот: <b>{chamber}</b>"],
    )

    await callback.answer("Крутим барабан")
    spin_msg = await callback.message.answer("🎡 Крутим барабан... ▫️▫️▫️")
    for frame in ["🎡 Крутим барабан... ▪️▫️▫️", "🎡 Крутим барабан... ▪️▪️▫️", "🎡 Крутим барабан... ▪️▪️▪️"]:
        await asyncio.sleep(0.45)
        try:
            await spin_msg.edit_text(frame)
        except TelegramBadRequest:
            pass

    outcome = resolve_roulette(stake, settings.house_edge_roulette, chamber)

    await repo.finalize_bet(
        bet_id=bet.id,
        status="won" if outcome.won else "lost",
        payout=outcome.payout,
        base_multiplier=outcome.base_multiplier,
        applied_edge=outcome.applied_edge,
        details=outcome.details,
    )
    await _post_result_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game="roulette",
        stake=stake,
        payout=outcome.payout,
        status="won" if outcome.won else "lost",
        bet_id=bet.id,
        extra_lines=[
            f"Слот: <b>{chamber}</b>",
            f"Результат: <b>{html.escape(str(outcome.message))}</b>",
        ],
    )
    text = _format_roulette_result_text(stake=stake, chamber=chamber, outcome=outcome)
    await _send_outcome_banner(
        message=callback.message,
        settings=settings,
        won=outcome.won,
        caption=text,
        reply_markup=replay_keyboard(game="roulette", stake=stake, choice=str(chamber)),
    )

    try:
        await spin_msg.edit_text("🎡 Барабан остановлен. Итог отправлен отдельным сообщением.")
    except TelegramBadRequest:
        pass
    await state.clear()


@router.callback_query(F.data.startswith("mines_count:"))
async def on_mines_count(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    data = await state.get_data()
    stake = _stake_from_state(data)
    game = data.get("game")
    if not stake or game != "mines":
        await callback.answer("Сначала выберите игру и ставку", show_alert=True)
        return

    mines_count = int(callback.data.split(":", maxsplit=1)[1])

    try:
        bet = await repo.place_bet(
            user_id=callback.from_user.id,
            game="mines",
            stake=stake,
            details={"mines": mines_count},
        )
    except BalanceError:
        await callback.message.answer("❌ Недостаточно средств на балансе")
        await state.clear()
        return

    state_payload = create_mines_state(mines_count)
    state_payload["stake"] = str(stake)

    game_session = await repo.create_game_session(
        user_id=callback.from_user.id,
        bet_id=bet.id,
        game="mines",
        state=state_payload,
    )
    await _post_bet_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game="mines",
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Мин на поле: <b>{mines_count}</b>"],
    )

    await callback.answer("Игра началась")
    await callback.message.answer(
        f"💣 <b>Мины</b>\n"
        f"Ставка: <b>{fmt_money(stake)}</b>\n"
        f"Мин на поле: <b>{mines_count}</b>\n"
        f"Текущий множитель: <b>x{state_payload['current_multiplier']}</b>\n"
        "Открывайте безопасные клетки или заберите выигрыш до подрыва.",
        reply_markup=mines_grid_keyboard(session_id=game_session.id, state=state_payload),
    )
    await state.clear()


@router.callback_query(F.data.startswith("mines_open:"))
async def on_mines_open(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    _, session_id, cell_s = callback.data.split(":", maxsplit=2)
    cell_idx = int(cell_s)

    game_session = await repo.get_game_session(session_id)
    if not game_session or game_session.status != "active":
        await callback.answer("Эта сессия уже завершена", show_alert=True)
        return

    if game_session.user_id != callback.from_user.id:
        await callback.answer("Это не ваша игра", show_alert=True)
        return

    mine_hit, multiplier, new_state = open_mines_cell(
        state=game_session.state,
        cell_index=cell_idx,
        edge=settings.house_edge_mines,
    )

    if mine_hit:
        await repo.finalize_bet(
            bet_id=game_session.bet_id,
            status="lost",
            payout=Decimal("0"),
            base_multiplier=Decimal("0"),
            applied_edge=Decimal(str(settings.house_edge_mines)),
            details={"mine_hit": True, "opened_cells": new_state.get("opened_cells", [])},
        )
        await repo.update_game_session(session_id=game_session.id, state=new_state, status="lost")
        await _post_result_to_channel(
            bot=callback.bot,
            settings=settings,
            repo=repo,
            user_id=callback.from_user.id,
            game="mines",
            stake=to_decimal(new_state["stake"]),
            payout=Decimal("0"),
            status="lost",
            bet_id=game_session.bet_id,
            extra_lines=[
                f"Мин на поле: <b>{new_state['mines_count']}</b>",
                "Исход: <b>Попадание на мину</b>",
            ],
        )
        await _send_outcome_banner(
            message=callback.message,
            settings=settings,
            won=False,
            caption=(
                "💣 <b>Мины</b>\n"
                f"Мин на поле: <b>{new_state['mines_count']}</b>\n"
                "Исход: <b>Попадание на мину</b>\n"
                f"❌ <b>Проигрыш: {fmt_money(new_state['stake'])}</b>"
            ),
            reply_markup=replay_keyboard(
                game="mines",
                stake=to_decimal(new_state["stake"]),
                choice=str(new_state["mines_count"]),
            ),
        )

        await callback.answer("Мина")
        await callback.message.edit_text(
            f"💣 <b>Мина</b>\nВы проиграли: <b>{fmt_money(new_state['stake'])}</b>",
            reply_markup=mines_grid_keyboard(
                session_id=game_session.id,
                state=new_state,
                reveal_all=True,
                interactive=False,
            ),
        )
        return

    safe_limit = 25 - int(new_state["mines_count"])
    safe_opened = len([i for i in new_state["opened_cells"] if i not in new_state["mine_cells"]])

    if safe_opened >= safe_limit:
        payout = mines_cashout(to_decimal(new_state["stake"]), multiplier)
        await repo.finalize_bet(
            bet_id=game_session.bet_id,
            status="won",
            payout=payout,
            base_multiplier=to_decimal(new_state["current_multiplier"]),
            applied_edge=Decimal(str(settings.house_edge_mines)),
            details={"auto_cashout": True, "opened_cells": new_state.get("opened_cells", [])},
        )
        await repo.update_game_session(session_id=game_session.id, state=new_state, status="won")
        await _post_result_to_channel(
            bot=callback.bot,
            settings=settings,
            repo=repo,
            user_id=callback.from_user.id,
            game="mines",
            stake=to_decimal(new_state["stake"]),
            payout=payout,
            status="won",
            bet_id=game_session.bet_id,
            extra_lines=[
                f"Мин на поле: <b>{new_state['mines_count']}</b>",
                f"Множитель: <b>x{new_state['current_multiplier']}</b>",
                "Исход: <b>Открыты все безопасные клетки</b>",
            ],
        )
        await _send_outcome_banner(
            message=callback.message,
            settings=settings,
            won=True,
            caption=(
                "🏆 <b>Мины</b>\n"
                f"Мин на поле: <b>{new_state['mines_count']}</b>\n"
                f"Множитель: <b>x{new_state['current_multiplier']}</b>\n"
                "Исход: <b>Открыты все безопасные клетки</b>\n"
                f"✅ <b>Выигрыш: {fmt_money(payout)}</b>"
            ),
            reply_markup=replay_keyboard(
                game="mines",
                stake=to_decimal(new_state["stake"]),
                choice=str(new_state["mines_count"]),
            ),
        )

        await callback.message.edit_text(
            f"🏆 <b>Все безопасные клетки открыты</b>\n"
            f"Выигрыш: <b>{fmt_money(payout)}</b>",
            reply_markup=mines_grid_keyboard(
                session_id=game_session.id,
                state=new_state,
                reveal_all=True,
                interactive=False,
            ),
        )
        await callback.answer("Максимальный выигрыш")
        return

    await repo.update_game_session(session_id=game_session.id, state=new_state)
    await callback.message.edit_text(
        f"💣 <b>Мины</b>\n"
        f"Ставка: <b>{fmt_money(new_state['stake'])}</b>\n"
        f"Мин: <b>{new_state['mines_count']}</b>\n"
        f"Текущий множитель: <b>x{new_state['current_multiplier']}</b>",
        reply_markup=mines_grid_keyboard(session_id=game_session.id, state=new_state),
    )
    await callback.answer(f"x{new_state['current_multiplier']}")


@router.callback_query(F.data.startswith("mines_cashout:"))
async def on_mines_cashout(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user:
        return

    session_id = callback.data.split(":", maxsplit=1)[1]
    game_session = await repo.get_game_session(session_id)
    if not game_session or game_session.status != "active":
        await callback.answer("Сессия недоступна", show_alert=True)
        return

    if game_session.user_id != callback.from_user.id:
        await callback.answer("Это не ваша игра", show_alert=True)
        return

    state_data = game_session.state
    stake = to_decimal(state_data["stake"])
    multiplier = to_decimal(state_data.get("current_multiplier", "1"))
    payout = mines_cashout(stake, multiplier)

    await repo.finalize_bet(
        bet_id=game_session.bet_id,
        status="won",
        payout=payout,
        base_multiplier=multiplier,
        applied_edge=Decimal(str(settings.house_edge_mines)),
        details={"manual_cashout": True, "opened_cells": state_data.get("opened_cells", [])},
    )
    await repo.update_game_session(session_id=game_session.id, state=state_data, status="cashed_out")
    await _post_result_to_channel(
        bot=callback.bot,
        settings=settings,
        repo=repo,
        user_id=callback.from_user.id,
        game="mines",
        stake=stake,
        payout=payout,
        status="won",
        bet_id=game_session.bet_id,
        extra_lines=[
            f"Мин на поле: <b>{state_data['mines_count']}</b>",
            f"Кэшаут на: <b>x{multiplier}</b>",
        ],
    )
    await _send_outcome_banner(
        message=callback.message,
        settings=settings,
        won=True,
        caption=(
            "🎁 <b>Мины: кэшаут</b>\n"
            f"Мин на поле: <b>{state_data['mines_count']}</b>\n"
            f"Кэшаут на: <b>x{multiplier}</b>\n"
            f"✅ <b>Выигрыш: {fmt_money(payout)}</b>"
        ),
        reply_markup=replay_keyboard(
            game="mines",
            stake=stake,
            choice=str(state_data["mines_count"]),
        ),
    )

    await callback.message.edit_text(
        f"🎁 <b>Кэшаут выполнен</b>\n"
        f"Ставка: <b>{fmt_money(stake)}</b>\n"
        f"Множитель: <b>x{multiplier}</b>\n"
        f"Выплата: <b>{fmt_money(payout)}</b>",
        reply_markup=mines_grid_keyboard(
            session_id=game_session.id,
            state=state_data,
            reveal_all=True,
            interactive=False,
        ),
    )
    await callback.answer("Выигрыш зачислен")


@router.message(Command("deposit"))
@router.message(F.text.in_(DEPOSIT_TEXTS))
async def deposit_menu(
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    crypto: CryptoBotClient,
) -> None:
    if not message.from_user:
        return

    await repo.ensure_user(message.from_user)

    stars_rate = await _get_stars_rate(repo, settings)
    crypto_status = "доступен" if crypto.enabled else "недоступен"
    stars_status = "доступны" if settings.enable_telegram_stars else "недоступны"
    crypto_assets = ", ".join(settings.cryptobot_assets)
    await message.answer(
        "💳 <b>Пополнение баланса</b>\n"
        f"CryptoBot: <b>{crypto_status}</b> ({html.escape(crypto_assets)})\n"
        f"Telegram Stars: <b>{stars_status}</b>\n"
        f"Курс Stars: <b>1 ⭐ = ${stars_rate:.4f}</b>.\n"
        "Выберите метод:",
        reply_markup=deposit_method_keyboard(
            crypto_enabled=crypto.enabled,
            stars_enabled=settings.enable_telegram_stars,
        ),
    )


@router.callback_query(F.data == "quick_deposit")
async def on_quick_deposit(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
    crypto: CryptoBotClient,
) -> None:
    await callback.answer()
    await state.clear()
    if not callback.message:
        return
    stars_rate = await _get_stars_rate(repo, settings)
    crypto_assets = ", ".join(settings.cryptobot_assets)
    text = (
        "💳 <b>Пополнение баланса</b>\n"
        f"Crypto активы: <b>{html.escape(crypto_assets)}</b>\n"
        f"Курс Stars: <b>1 ⭐ = ${stars_rate:.4f}</b>\n"
        "Выберите метод оплаты:"
    )
    await _edit_or_send(
        callback.message,
        text,
        reply_markup=deposit_method_keyboard(
            crypto_enabled=crypto.enabled,
            stars_enabled=settings.enable_telegram_stars,
        ),
    )


@router.callback_query(F.data == "quick_withdraw")
async def on_quick_withdraw(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
) -> None:
    if not callback.from_user or not callback.message:
        return

    await repo.ensure_user(callback.from_user)
    await callback.answer()
    await state.set_state(BetFlow.waiting_withdraw)
    await _edit_or_send(
        callback.message,
        "💸 <b>Вывод средств</b>\n"
        "Введите сумму в USD, которую хотите вывести.\n"
        "Пример: <code>15</code>\n\n"
        "После этого будет создана заявка администратору.",
        reply_markup=back_keyboard(),
    )


@router.callback_query(F.data.startswith("deposit_method:"))
async def on_deposit_method(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
    crypto: CryptoBotClient,
) -> None:
    if not callback.message:
        return
    await state.clear()
    method = callback.data.split(":", maxsplit=1)[1]

    if method == "crypto":
        if not crypto.enabled:
            await callback.answer("CryptoBot отключен", show_alert=True)
            return
        await _edit_or_send(
            callback.message,
            "🟢 <b>CryptoBot</b>\nВыберите монету для оплаты:",
            reply_markup=deposit_crypto_asset_keyboard(assets=settings.cryptobot_assets),
        )
        await callback.answer()
        return

    if method == "stars":
        if not settings.enable_telegram_stars:
            await callback.answer("Пополнение Stars отключено", show_alert=True)
            return
        stars_rate = await _get_stars_rate(repo, settings)
        await _edit_or_send(
            callback.message,
            "⭐ <b>Telegram Stars</b>\n"
            f"Курс: 1 ⭐ = ${stars_rate:.4f}.\n"
            "Выберите сумму или введите свою:",
            reply_markup=stars_amount_keyboard(stars_rate),
        )
        await callback.answer()
        return

    await callback.answer("Неизвестный метод", show_alert=True)


@router.callback_query(F.data.startswith("deposit_crypto_asset:"))
async def on_deposit_crypto_asset(
    callback: CallbackQuery,
    settings: Settings,
) -> None:
    if not callback.message:
        return
    asset = callback.data.split(":", maxsplit=1)[1].upper()
    if asset not in settings.cryptobot_assets:
        await callback.answer("Монета не поддерживается", show_alert=True)
        return
    await callback.answer()
    await _edit_or_send(
        callback.message,
        f"🧾 <b>Пополнение через {asset}</b>\n"
        "Выберите сумму или введите свою:",
        reply_markup=deposit_crypto_amount_keyboard(asset),
    )


@router.callback_query(F.data.startswith("deposit_amount:"))
async def on_deposit_amount(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
    crypto: CryptoBotClient,
) -> None:
    if not callback.from_user or not callback.message:
        return

    if not crypto.enabled:
        await callback.answer("Пополнение недоступно", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) == 2:
        amount = to_decimal(parts[1])
        asset = settings.cryptobot_default_asset.upper()
    elif len(parts) == 3:
        _, asset, amount_s = parts
        amount = to_decimal(amount_s)
        asset = asset.upper()
    else:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    if amount <= 0:
        await callback.answer("Некорректная сумма", show_alert=True)
        return
    if asset not in settings.cryptobot_assets:
        await callback.answer("Монета не поддерживается", show_alert=True)
        return

    created = await _create_crypto_invoice(
        message=callback.message,
        repo=repo,
        crypto=crypto,
        user_id=callback.from_user.id,
        amount=amount,
        asset=asset,
    )
    if created:
        await callback.answer("Инвойс создан")
    else:
        await callback.answer("Ошибка создания инвойса", show_alert=True)


@router.callback_query(F.data.startswith("deposit_crypto_manual:"))
async def on_deposit_crypto_manual(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
) -> None:
    if not callback.message:
        return
    asset = callback.data.split(":", maxsplit=1)[1].upper()
    if asset not in settings.cryptobot_assets:
        await callback.answer("Монета не поддерживается", show_alert=True)
        return
    await state.set_state(BetFlow.waiting_deposit_crypto_amount)
    await state.update_data(deposit_asset=asset)
    await callback.answer()
    await callback.message.answer(
        f"✍️ Введите сумму для пополнения в <b>{asset}</b>.\n"
        "Пример: <code>12.5</code>",
        reply_markup=back_keyboard(callback_data="quick_deposit"),
    )


@router.message(BetFlow.waiting_deposit_crypto_amount)
async def on_deposit_crypto_amount_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
    crypto: CryptoBotClient,
) -> None:
    if not message.from_user:
        return
    if not crypto.enabled:
        await state.clear()
        await message.answer("❌ CryptoBot отключен.")
        return

    state_data = await state.get_data()
    asset = str(state_data.get("deposit_asset", settings.cryptobot_default_asset)).upper()
    if asset not in settings.cryptobot_assets:
        asset = settings.cryptobot_default_asset.upper()

    amount = _parse_amount(message.text or "")
    if amount is None or amount <= 0:
        await message.answer("❌ Введите корректную сумму. Пример: <code>5</code>")
        return

    created = await _create_crypto_invoice(
        message=message,
        repo=repo,
        crypto=crypto,
        user_id=message.from_user.id,
        amount=amount,
        asset=asset,
    )
    if created:
        await state.clear()


@router.callback_query(F.data == "deposit_stars_manual")
async def on_deposit_stars_manual(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    repo: CasinoRepository,
) -> None:
    if not callback.message:
        return
    if not settings.enable_telegram_stars:
        await callback.answer("Пополнение Stars отключено", show_alert=True)
        return
    stars_rate = await _get_stars_rate(repo, settings)
    await state.set_state(BetFlow.waiting_deposit_stars_amount)
    await callback.answer()
    await callback.message.answer(
        f"✍️ Введите сумму в Stars.\nКурс: <b>1 ⭐ = ${stars_rate:.4f}</b>\n"
        "Пример: <code>125</code>",
        reply_markup=back_keyboard(callback_data="quick_deposit"),
    )


@router.message(BetFlow.waiting_deposit_stars_amount)
async def on_deposit_stars_amount_input(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return
    if not settings.enable_telegram_stars:
        await state.clear()
        await message.answer("❌ Пополнение Stars отключено.")
        return

    stars = _parse_positive_int(message.text or "")
    if stars is None or stars <= 0:
        await message.answer("❌ Введите целое число Stars. Пример: <code>100</code>")
        return

    await _send_stars_invoice(
        message=message,
        repo=repo,
        settings=settings,
        user_id=message.from_user.id,
        stars=stars,
    )
    await state.clear()


@router.callback_query(F.data.startswith("deposit_stars:"))
async def on_deposit_stars(
    callback: CallbackQuery,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        return

    if not settings.enable_telegram_stars:
        await callback.answer("Пополнение Stars отключено", show_alert=True)
        return

    stars = int(callback.data.split(":", maxsplit=1)[1])
    if stars <= 0:
        await callback.answer("Некорректная сумма", show_alert=True)
        return

    await _send_stars_invoice(
        message=callback.message,
        repo=repo,
        settings=settings,
        user_id=callback.from_user.id,
        stars=stars,
    )
    await callback.answer("Счёт Stars отправлен")


async def _create_crypto_invoice(
    *,
    message: Message,
    repo: CasinoRepository,
    crypto: CryptoBotClient,
    user_id: int,
    amount: Decimal,
    asset: str,
) -> bool:
    payload = f"dep_{user_id}_{asset}_{uuid.uuid4().hex[:8]}"
    try:
        invoice = await crypto.create_invoice(
            amount=str(amount),
            asset=asset,
            description=f"Пополнение баланса {user_id}",
            payload=payload,
        )

        external_invoice_id = int(invoice.get("invoice_id") or invoice.get("id") or 0)
        pay_url = (
            invoice.get("pay_url")
            or invoice.get("bot_invoice_url")
            or invoice.get("mini_app_invoice_url")
            or invoice.get("web_app_invoice_url")
        )

        if not external_invoice_id or not pay_url:
            raise CryptoBotError("CryptoBot вернул неполные данные инвойса")

        await repo.create_invoice(
            user_id=user_id,
            invoice_id=external_invoice_id,
            amount=amount,
            asset=asset,
            pay_url=pay_url,
            payload=payload,
        )

        await message.answer(
            f"🧾 <b>Счёт создан</b>\n"
            f"Сумма: <b>{amount} {asset}</b>\n"
            "После оплаты баланс зачислится автоматически.",
            reply_markup=invoice_keyboard(pay_url),
        )
        return True
    except CryptoBotError as err:
        await message.answer(f"Ошибка CryptoBot: {err}")
        return False


async def _send_stars_invoice(
    *,
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    stars: int,
) -> None:
    stars_rate = await _get_stars_rate(repo, settings)
    usd_amount = q_money(Decimal(stars) * stars_rate)
    payload = f"stars_dep_{user_id}_{stars}_{uuid.uuid4().hex[:8]}"
    await message.answer_invoice(
        title="Пополнение баланса",
        description=f"{stars} Stars -> зачисление {fmt_money(usd_amount)} на баланс",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} Stars", amount=stars)],
    )


@router.callback_query(F.data == "deposit_check")
async def on_manual_deposit_check(
    callback: CallbackQuery,
    repo: CasinoRepository,
    crypto: CryptoBotClient,
) -> None:
    if not callback.from_user or not callback.message:
        return

    if not crypto.enabled:
        await callback.answer("CryptoBot отключен", show_alert=True)
        return

    invoices = await repo.get_user_invoices(user_id=callback.from_user.id, limit=25)

    if not invoices:
        await callback.answer("У вас пока нет созданных счетов", show_alert=True)
        return

    try:
        remote = await crypto.get_invoices(invoice_ids=[i.invoice_id for i in invoices])
    except CryptoBotError as err:
        await callback.answer("Ошибка проверки", show_alert=True)
        await callback.message.answer(f"CryptoBot error: {html.escape(str(err))}")
        return

    credited = 0
    for item in remote:
        invoice_id = int(item.get("invoice_id", 0))
        status = str(item.get("status", "")).lower().strip()
        if not invoice_id or not status:
            continue
        changed = await repo.apply_paid_invoice(
            external_invoice_id=invoice_id,
            external_status=status,
        )
        if changed:
            credited += 1

    if credited:
        profile = await repo.get_profile(callback.from_user.id)
        await callback.message.answer(
            f"✅ Зачислено оплат: <b>{credited}</b>\n"
            f"Новый баланс: <b>{fmt_money(profile.balance)}</b>"
        )
    else:
        refreshed = await repo.get_user_invoices(user_id=callback.from_user.id, limit=25)
        paid_count = sum(1 for inv in refreshed if inv.status == "paid")
        open_count = sum(1 for inv in refreshed if inv.status in {"active", "pending"})

        if paid_count > 0:
            profile = await repo.get_profile(callback.from_user.id)
            await callback.message.answer(
                "✅ Оплата уже зачислена ранее.\n"
                f"Текущий баланс: <b>{fmt_money(profile.balance)}</b>"
            )
        elif open_count > 0:
            await callback.message.answer(
                "⌛ Новых оплаченных счетов пока нет.\n"
                "Если вы только что оплатили, подождите 10-20 секунд и нажмите «Проверить оплату» снова."
            )
        else:
            await callback.message.answer(
                "ℹ️ Активных счетов сейчас нет (последние счета завершены или истекли)."
            )

    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user or not message.successful_payment:
        return

    payment = message.successful_payment
    payload = payment.invoice_payload or ""

    if payment.currency != "XTR" or not payload.startswith("stars_dep_"):
        return

    stars_rate = await _get_stars_rate(repo, settings)
    stars_amount = Decimal(payment.total_amount)
    amount = q_money(stars_amount * stars_rate)
    external_id = (
        payment.telegram_payment_charge_id
        or payment.provider_payment_charge_id
        or payload
    )

    already_credited = await repo.transaction_exists(
        kind="deposit_stars",
        external_id=external_id,
        user_id=message.from_user.id,
    )
    if already_credited:
        await message.answer("ℹ️ Этот Stars-платеж уже был зачислен.")
        return

    await repo.credit_balance(
        user_id=message.from_user.id,
        amount=amount,
        kind="deposit_stars",
        external_id=external_id,
        description="Пополнение через Telegram Stars",
        details={
            "payload": payload,
            "currency": payment.currency,
            "stars_amount": str(stars_amount),
            "rate": float(stars_rate),
        },
    )
    await message.answer(
        f"⭐ <b>Stars зачислены</b>\n"
        f"Получено: <b>{int(stars_amount)} ⭐</b>\n"
        f"Зачислено: <b>{fmt_money(amount)}</b>"
    )


@router.message(Command("withdraw"))
@router.message(F.text.in_(WITHDRAW_TEXTS))
async def withdraw_prompt(message: Message, state: FSMContext, repo: CasinoRepository) -> None:
    if not message.from_user:
        return

    await repo.ensure_user(message.from_user)
    await _send_withdraw_prompt(message, state)


@router.message(BetFlow.waiting_withdraw)
async def on_withdraw_submit(
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not message.from_user:
        return

    amount = _parse_amount(message.text or "")
    if amount is None or amount <= 0:
        await message.answer("❌ Укажите сумму в USD. Пример: <code>15</code>")
        return

    profile_before = await repo.get_profile(message.from_user.id)
    if profile_before.balance < amount:
        await message.answer("❌ Недостаточно средств")
        await state.clear()
        return

    try:
        request = await repo.create_withdraw_request(
            user_id=message.from_user.id,
            amount=amount,
            details={"username": message.from_user.username, "first_name": message.from_user.first_name},
        )
    except BalanceError:
        await message.answer("❌ Недостаточно средств")
        await state.clear()
        return

    profile_after = await repo.get_profile(message.from_user.id)
    admins = await _get_admin_chat_ids(repo, settings)
    admin_text = (
        "📥 <b>Новая заявка на вывод</b>\n"
        f"Заявка: <code>{request.id}</code>\n"
        f"Пользователь: {await _channel_player_line(repo, message.from_user.id)}\n"
        f"Сумма: <b>{fmt_money(amount)}</b>\n"
        f"Баланс после списания: <b>{fmt_money(profile_after.balance)}</b>"
    )
    sent_to = 0
    for admin_id in admins:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                reply_markup=admin_withdraw_request_alert_keyboard(request_id=request.id),
            )
            sent_to += 1
        except Exception:
            continue

    await message.answer(
        "✅ <b>Заявка на вывод создана</b>\n"
        f"Сумма: <b>{fmt_money(amount)}</b>\n"
        f"Номер заявки: <code>{request.id}</code>\n"
        f"Списано с баланса: <b>{fmt_money(amount)}</b>\n"
        f"Новый баланс: <b>{fmt_money(profile_after.balance)}</b>\n"
        "Ожидайте подтверждения администратора."
    )
    if sent_to == 0:
        await message.answer("⚠️ Админ пока не получил уведомление. Сообщите в техподдержку.")
    await state.clear()


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("repeat_bet:"))
async def on_repeat_bet(
    callback: CallbackQuery,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        return

    parts = callback.data.split(":", maxsplit=3)
    if len(parts) != 4:
        await callback.answer("Не удалось повторить ставку", show_alert=True)
        return

    _, game, stake_raw, choice_raw = parts
    stake = _parse_amount(stake_raw)
    if stake is None:
        await callback.answer("Некорректная сумма", show_alert=True)
        return

    if stake < q_money(settings.min_bet) or stake > q_money(settings.max_bet):
        await callback.answer(
            f"Ставка вне диапазона {fmt_money(settings.min_bet)}..{fmt_money(settings.max_bet)}",
            show_alert=True,
        )
        return

    choice = None if choice_raw == "_" else choice_raw
    await callback.answer("Повторяем ставку")
    await state.clear()

    if game == "slots":
        await _run_slots(callback.message, repo, settings, callback.from_user.id, stake)
        return

    if game == "crash":
        await _start_crash_round(
            message=callback.message,
            repo=repo,
            settings=settings,
            user_id=callback.from_user.id,
            stake=stake,
        )
        return

    if game == "dice":
        if not choice:
            await callback.message.answer("Невозможно повторить: не найден исход.")
            return
        await _run_dice_round(
            message=callback.message,
            repo=repo,
            settings=settings,
            user_id=callback.from_user.id,
            stake=stake,
            choice=choice,
        )
        return

    if game in EMOJI_BY_GAME:
        if not choice:
            await callback.message.answer("Невозможно повторить: не найден исход.")
            return
        await _run_emoji_round(
            message=callback.message,
            repo=repo,
            settings=settings,
            user_id=callback.from_user.id,
            game_name=game,
            stake=stake,
            choice=choice,
        )
        return

    if game == "roulette":
        if not choice or not choice.isdigit():
            await callback.message.answer("Невозможно повторить: не найден слот.")
            return
        chamber = int(choice)
        await _run_roulette_round(
            message=callback.message,
            repo=repo,
            settings=settings,
            user_id=callback.from_user.id,
            stake=stake,
            chamber=chamber,
        )
        return

    if game == "mines":
        if not choice or not choice.isdigit():
            await callback.message.answer("Невозможно повторить: не найдено число мин.")
            return
        mines_count = int(choice)
        await _run_mines_round(
            message=callback.message,
            repo=repo,
            settings=settings,
            user_id=callback.from_user.id,
            stake=stake,
            mines_count=mines_count,
        )
        return

    await callback.message.answer("Эта игра сейчас недоступна для повтора.")


async def _run_dice_round(
    *,
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    stake: Decimal,
    choice: str,
) -> None:
    try:
        bet = await repo.place_bet(
            user_id=user_id,
            game="dice",
            stake=stake,
            details={"choice": choice},
        )
    except BalanceError:
        await message.answer("❌ Недостаточно средств на балансе")
        return

    await _post_bet_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="dice",
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Исход: <b>{html.escape(_dice_choice_title(choice))}</b>"],
    )

    if choice == "duel":
        player_dice = await message.answer_dice(emoji=DICE_EMOJI)
        await asyncio.sleep(0.35)
        bot_dice = await message.answer_dice(emoji=DICE_EMOJI)
        player_value = player_dice.dice.value if player_dice.dice else 1
        bot_value = bot_dice.dice.value if bot_dice.dice else 1
        outcome = resolve_dice_duel(
            stake=stake,
            edge=settings.house_edge_dice,
            player_value=player_value,
            bot_value=bot_value,
        )
    else:
        dice_msg = await message.answer_dice(emoji=DICE_EMOJI)
        result_value = dice_msg.dice.value if dice_msg.dice else 1
        outcome = resolve_dice(stake, settings.house_edge_dice, result_value, choice)

    status = "won" if outcome.won else "lost"
    if outcome.details.get("duel_result") == "draw":
        status = "push"

    await repo.finalize_bet(
        bet_id=bet.id,
        status=status,
        payout=outcome.payout,
        base_multiplier=outcome.base_multiplier,
        applied_edge=outcome.applied_edge,
        details=outcome.details,
    )
    await _post_result_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="dice",
        stake=stake,
        payout=outcome.payout,
        status=status,
        bet_id=bet.id,
        extra_lines=_channel_dice_result_lines(outcome),
    )
    text = _format_dice_result_text(stake=stake, outcome=outcome)
    replay_markup = replay_keyboard(game="dice", stake=stake, choice=choice)
    if status in {"won", "lost"}:
        await _send_outcome_banner(
            message=message,
            settings=settings,
            won=status == "won",
            caption=text,
            reply_markup=replay_markup,
        )
    else:
        await message.answer(text, reply_markup=replay_markup)


async def _run_emoji_round(
    *,
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    game_name: str,
    stake: Decimal,
    choice: str,
) -> None:
    try:
        bet = await repo.place_bet(
            user_id=user_id,
            game=game_name,
            stake=stake,
            details={"choice": choice},
        )
    except BalanceError:
        await message.answer("❌ Недостаточно средств на балансе")
        return

    await _post_bet_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game=game_name,
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Исход: <b>{html.escape(_emoji_choice_title(game_name, choice))}</b>"],
    )

    emoji_msg = await message.answer_dice(emoji=EMOJI_BY_GAME[game_name])
    result_value = emoji_msg.dice.value if emoji_msg.dice else 1
    outcome = resolve_emoji_game(
        stake=stake,
        edge=settings.house_edge_dice,
        game=game_name,
        dice_value=result_value,
        choice=choice,
    )

    await repo.finalize_bet(
        bet_id=bet.id,
        status="won" if outcome.won else "lost",
        payout=outcome.payout,
        base_multiplier=outcome.base_multiplier,
        applied_edge=outcome.applied_edge,
        details=outcome.details,
    )
    await _post_result_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game=game_name,
        stake=stake,
        payout=outcome.payout,
        status="won" if outcome.won else "lost",
        bet_id=bet.id,
        extra_lines=_channel_emoji_result_lines(outcome),
    )
    text = _format_emoji_result_text(game_name=game_name, stake=stake, outcome=outcome)
    await _send_outcome_banner(
        message=message,
        settings=settings,
        won=outcome.won,
        caption=text,
        reply_markup=replay_keyboard(game=game_name, stake=stake, choice=choice),
    )


async def _run_roulette_round(
    *,
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    stake: Decimal,
    chamber: int,
) -> None:
    if chamber < 1 or chamber > 6:
        await message.answer("Некорректный слот рулетки.")
        return

    try:
        bet = await repo.place_bet(
            user_id=user_id,
            game="roulette",
            stake=stake,
            details={"chamber": chamber},
        )
    except BalanceError:
        await message.answer("❌ Недостаточно средств на балансе")
        return

    await _post_bet_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="roulette",
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Слот: <b>{chamber}</b>"],
    )

    spin_msg = await message.answer("🎡 Крутим барабан... ▫️▫️▫️")
    for frame in ["🎡 Крутим барабан... ▪️▫️▫️", "🎡 Крутим барабан... ▪️▪️▫️", "🎡 Крутим барабан... ▪️▪️▪️"]:
        await asyncio.sleep(0.45)
        try:
            await spin_msg.edit_text(frame)
        except TelegramBadRequest:
            pass

    outcome = resolve_roulette(stake, settings.house_edge_roulette, chamber)

    await repo.finalize_bet(
        bet_id=bet.id,
        status="won" if outcome.won else "lost",
        payout=outcome.payout,
        base_multiplier=outcome.base_multiplier,
        applied_edge=outcome.applied_edge,
        details=outcome.details,
    )
    await _post_result_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="roulette",
        stake=stake,
        payout=outcome.payout,
        status="won" if outcome.won else "lost",
        bet_id=bet.id,
        extra_lines=[
            f"Слот: <b>{chamber}</b>",
            f"Результат: <b>{html.escape(str(outcome.message))}</b>",
        ],
    )
    text = _format_roulette_result_text(stake=stake, chamber=chamber, outcome=outcome)
    await _send_outcome_banner(
        message=message,
        settings=settings,
        won=outcome.won,
        caption=text,
        reply_markup=replay_keyboard(game="roulette", stake=stake, choice=str(chamber)),
    )

    try:
        await spin_msg.edit_text("🎡 Барабан остановлен. Итог отправлен отдельным сообщением.")
    except TelegramBadRequest:
        pass


async def _run_mines_round(
    *,
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    stake: Decimal,
    mines_count: int,
) -> None:
    if mines_count <= 0 or mines_count >= 25:
        await message.answer("Некорректное количество мин.")
        return

    try:
        bet = await repo.place_bet(
            user_id=user_id,
            game="mines",
            stake=stake,
            details={"mines": mines_count},
        )
    except BalanceError:
        await message.answer("❌ Недостаточно средств на балансе")
        return

    state_payload = create_mines_state(mines_count)
    state_payload["stake"] = str(stake)
    game_session = await repo.create_game_session(
        user_id=user_id,
        bet_id=bet.id,
        game="mines",
        state=state_payload,
    )
    await _post_bet_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="mines",
        stake=stake,
        bet_id=bet.id,
        extra_lines=[f"Мин на поле: <b>{mines_count}</b>"],
    )

    await message.answer(
        f"💣 <b>Мины</b>\n"
        f"Ставка: <b>{fmt_money(stake)}</b>\n"
        f"Мин на поле: <b>{mines_count}</b>\n"
        f"Текущий множитель: <b>x{state_payload['current_multiplier']}</b>\n"
        "Открывайте безопасные клетки или заберите выигрыш.",
        reply_markup=mines_grid_keyboard(session_id=game_session.id, state=state_payload),
    )


async def _start_crash_round(
    *,
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    stake: Decimal,
) -> None:
    active = await repo.get_active_game_session(user_id=user_id, game="crash")
    if active:
        await message.answer("⚠️ У вас уже есть активный раунд Crash.")
        return

    try:
        bet = await repo.place_bet(
            user_id=user_id,
            game="crash",
            stake=stake,
            details={},
        )
    except BalanceError:
        await message.answer("❌ Недостаточно средств на балансе")
        return

    await _post_bet_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="crash",
        stake=stake,
        bet_id=bet.id,
        extra_lines=["Режим: <b>ручной кэшаут</b>"],
    )

    crash_point = generate_crash_point()
    state_payload = {
        "stake": str(stake),
        "current_multiplier": "1.00",
        "crash_point": str(crash_point),
        "tick": 0,
        "phase": "countdown",
        "result": "running",
    }
    game_session = await repo.create_game_session(
        user_id=user_id,
        bet_id=bet.id,
        game="crash",
        state=state_payload,
    )

    sent = await message.answer(
        _render_crash_countdown(stake=stake, seconds_left=5),
    )
    state_payload["chat_id"] = sent.chat.id
    state_payload["message_id"] = sent.message_id
    await repo.update_game_session(session_id=game_session.id, state=state_payload)

    for seconds_left in [4, 3, 2, 1]:
        await asyncio.sleep(1.0)
        current = await repo.get_game_session(game_session.id)
        if not current or current.status != "active":
            return
        try:
            await message.bot.edit_message_text(
                chat_id=sent.chat.id,
                message_id=sent.message_id,
                text=_render_crash_countdown(stake=stake, seconds_left=seconds_left),
            )
        except TelegramBadRequest:
            return

    state_payload["phase"] = "running"
    state_payload["current_multiplier"] = str(CRASH_START_MULTIPLIER)
    state_payload["tick"] = 0
    await repo.update_game_session(session_id=game_session.id, state=state_payload)
    try:
        effective_start = effective_multiplier_with_edge(
            CRASH_START_MULTIPLIER, settings.house_edge_crash
        )
        await message.bot.edit_message_text(
            chat_id=sent.chat.id,
            message_id=sent.message_id,
            text=_render_crash_text(
                stake=stake,
                current_multiplier=CRASH_START_MULTIPLIER,
                edge=settings.house_edge_crash,
                tick=0,
            ),
            reply_markup=crash_cashout_keyboard(game_session.id, f"{effective_start}"),
        )
    except TelegramBadRequest:
        return

    task = asyncio.create_task(
        _run_crash_round_loop(
            bot=message.bot,
            repo=repo,
            settings=settings,
            session_id=game_session.id,
        ),
        name=f"crash-round-{game_session.id}",
    )
    CRASH_TASKS[game_session.id] = task


async def _run_crash_round_loop(
    *,
    bot: Bot,
    repo: CasinoRepository,
    settings: Settings,
    session_id: str,
) -> None:
    try:
        first_tick = True
        while True:
            if not first_tick:
                await asyncio.sleep(CRASH_TICK_SEC)
            first_tick = False
            game_session = await repo.get_game_session(session_id)
            if not game_session or game_session.status != "active":
                return

            state_data = game_session.state
            phase = state_data.get("phase", "running")
            if phase != "running":
                continue
            stake = to_decimal(state_data.get("stake", "0"))
            current_multiplier = to_decimal(state_data.get("current_multiplier", "1.00"))
            crash_point = to_decimal(state_data.get("crash_point", "1.00"))
            tick = int(state_data.get("tick", 0))
            chat_id = state_data.get("chat_id")
            message_id = state_data.get("message_id")
            if not chat_id or not message_id:
                return

            if current_multiplier >= crash_point:
                effective_crash_point = effective_multiplier_with_edge(
                    crash_point, settings.house_edge_crash
                )
                await repo.finalize_bet(
                    bet_id=game_session.bet_id,
                    status="lost",
                    payout=Decimal("0"),
                    base_multiplier=Decimal("0"),
                    applied_edge=Decimal(str(settings.house_edge_crash)),
                    details={
                        "manual_cashout": False,
                        "crash_point": str(crash_point),
                    },
                )
                await _post_result_to_channel(
                    bot=bot,
                    settings=settings,
                    repo=repo,
                    user_id=game_session.user_id,
                    game="crash",
                    stake=stake,
                    payout=Decimal("0"),
                    status="lost",
                    bet_id=game_session.bet_id,
                    extra_lines=[f"Точка взрыва: <b>x{effective_crash_point}</b>"],
                )
                await _send_outcome_banner_to_chat(
                    bot=bot,
                    chat_id=int(chat_id),
                    settings=settings,
                    won=False,
                    caption=(
                        "💥 <b>Crash завершен</b>\n"
                        f"Ставка: <b>{fmt_money(stake)}</b>\n"
                        f"Взрыв на: <b>x{effective_crash_point}</b>\n"
                        f"❌ <b>Проигрыш: {fmt_money(stake)}</b>"
                    ),
                    reply_markup=replay_keyboard(game="crash", stake=stake),
                )
                state_data["current_multiplier"] = str(crash_point)
                state_data["tick"] = tick + 1
                state_data["phase"] = "finished"
                state_data["result"] = "crashed"
                await repo.update_game_session(
                    session_id=game_session.id,
                    state=state_data,
                    status="crashed",
                )
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="",
                    )
                except TelegramBadRequest:
                    pass
                return

            next_multiplier = next_crash_multiplier(current_multiplier)
            if next_multiplier > crash_point:
                next_multiplier = crash_point
            next_tick = tick + 1

            state_data["current_multiplier"] = str(next_multiplier)
            state_data["tick"] = next_tick
            await repo.update_game_session(session_id=game_session.id, state=state_data)
            try:
                effective_next_multiplier = effective_multiplier_with_edge(
                    next_multiplier, settings.house_edge_crash
                )
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=_render_crash_text(
                        stake=stake,
                        current_multiplier=next_multiplier,
                        edge=settings.house_edge_crash,
                        tick=next_tick,
                    ),
                    reply_markup=crash_cashout_keyboard(
                        session_id,
                        f"{effective_next_multiplier}",
                    ),
                )
            except TelegramBadRequest:
                return
    finally:
        CRASH_TASKS.pop(session_id, None)


def _cancel_crash_task(session_id: str) -> None:
    task = CRASH_TASKS.pop(session_id, None)
    if task and not task.done():
        task.cancel()


def _render_crash_countdown(*, stake: Decimal, seconds_left: int) -> str:
    dots = "•" * max(1, (5 - seconds_left + 1))
    return (
        "🚀 <b>Crash готовится к старту</b>\n"
        f"Ставка: <b>{fmt_money(stake)}</b>\n"
        f"Старт через: <b>{seconds_left} сек</b> {dots}\n\n"
        "⚠️ Как только полет начнется, x начнет расти сразу. Нажимайте <b>Забрать</b> вовремя."
    )


def _rocket_path_frame(tick: int) -> str:
    track_len = 9
    pos = tick % track_len
    left = "·" * pos
    right = "·" * (track_len - pos - 1)
    return f"{left}🚀{right}"


def _render_crash_text(
    *,
    stake: Decimal,
    current_multiplier: Decimal,
    edge: float,
    tick: int,
) -> str:
    effective_current = effective_multiplier_with_edge(current_multiplier, edge)
    potential = payout_with_edge(stake, current_multiplier, edge)
    frame = _rocket_path_frame(tick)
    return (
        "🚀 <b>Crash в процессе</b>\n"
        f"{frame}\n"
        f"Ставка: <b>{fmt_money(stake)}</b>\n"
        f"Текущий множитель к выплате: <b>x{effective_current}</b>\n"
        f"Потенциальный кэшаут: <b>{fmt_money(potential)}</b>\n\n"
        "Нажмите <b>Забрать</b> до взрыва."
    )


async def _apply_stake_selection(
    *,
    message: Message,
    state: FSMContext,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    stake: Decimal,
) -> None:
    data = await state.get_data()
    game = data.get("game")
    if not game:
        await _edit_or_send(
            message,
            "⌛ Сессия выбора игры устарела, выберите игру снова: /games",
            reply_markup=main_menu_inline_keyboard(),
        )
        await state.clear()
        return

    await state.update_data(stake=str(stake))

    if game == "slots":
        await _run_slots(message, repo, settings, user_id, stake)
        await state.clear()
        return

    if game == "dice":
        await _edit_or_send(
            message,
            f"🎲 <b>Кости</b>\nСтавка: <b>{fmt_money(stake)}</b>\n"
            "Выберите исход:",
            reply_markup=dice_choice_keyboard(settings.house_edge_dice),
        )
        return

    if game in {"football", "basketball", "darts", "bowling"}:
        await _edit_or_send(
            message,
            f"{GAME_LABELS[game]}\n"
            f"Ставка: <b>{fmt_money(stake)}</b>\n"
            "Выберите исход:",
            reply_markup=emoji_game_choice_keyboard(game, settings.house_edge_dice),
        )
        return

    if game == "crash":
        await _start_crash_round(
            message=message,
            repo=repo,
            settings=settings,
            user_id=user_id,
            stake=stake,
        )
        await state.clear()
        return

    if game == "roulette":
        await _edit_or_send(
            message,
            f"💀 <b>Русская рулетка</b>\nСтавка: <b>{fmt_money(stake)}</b>\n"
            "Выберите слот:",
            reply_markup=roulette_keyboard(),
        )
        return

    if game == "mines":
        await _edit_or_send(
            message,
            f"💣 <b>Мины</b>\nСтавка: <b>{fmt_money(stake)}</b>\n"
            "Выберите количество бомб:",
            reply_markup=mines_count_keyboard(),
        )
        return

    await _edit_or_send(message, "Эта игра пока не поддерживается")
    await state.clear()


async def _edit_or_send(
    message: Message | None,
    text: str,
    *,
    reply_markup=None,
) -> None:
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as err:
        if "message is not modified" in str(err).lower():
            return
        pass

    try:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as err:
        if "message is not modified" in str(err).lower():
            return
        pass

    await message.answer(text, reply_markup=reply_markup)

async def _run_slots(
    message: Message,
    repo: CasinoRepository,
    settings: Settings,
    user_id: int,
    stake: Decimal,
) -> None:
    await message.answer(
        f"🎰 <b>Слоты</b>\nСтавка: <b>{fmt_money(stake)}</b>\n"
        "Правила: платит только комбинация <b>777</b> с множителем <b>x10</b>."
    )

    try:
        bet = await repo.place_bet(
            user_id=user_id,
            game="slots",
            stake=stake,
            details={},
        )
    except BalanceError:
        await message.answer("❌ Недостаточно средств на балансе")
        return

    await _post_bet_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="slots",
        stake=stake,
        bet_id=bet.id,
        extra_lines=["Правило: <b>платит только 777 (x10)</b>"],
    )

    slot_spin = await message.answer_dice(emoji="🎰")
    slot_value = slot_spin.dice.value if slot_spin.dice else None

    outcome = spin_slots(stake, settings.house_edge_slots, slot_value)

    await repo.finalize_bet(
        bet_id=bet.id,
        status="won" if outcome.won else "lost",
        payout=outcome.payout,
        base_multiplier=outcome.base_multiplier,
        applied_edge=outcome.applied_edge,
        details=outcome.details,
    )
    combo = "777" if outcome.won else "не 777"
    await _post_result_to_channel(
        bot=message.bot,
        settings=settings,
        repo=repo,
        user_id=user_id,
        game="slots",
        stake=stake,
        payout=outcome.payout,
        status="won" if outcome.won else "lost",
        bet_id=bet.id,
        extra_lines=[
            f"Комбинация: <b>{combo}</b>",
            "Правило: <b>выигрыш только за 777</b>",
        ],
    )
    text = _format_slots_result_text(stake=stake, outcome=outcome)
    await _send_outcome_banner(
        message=message,
        settings=settings,
        won=outcome.won,
        caption=text,
        reply_markup=replay_keyboard(game="slots", stake=stake),
    )


async def _send_withdraw_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(BetFlow.waiting_withdraw)
    await message.answer(
        "💸 <b>Вывод средств</b>\n"
        "Введите сумму в USD, которую хотите вывести.\n"
        "Пример: <code>15</code>\n\n"
        "После этого будет создана заявка администратору.",
        reply_markup=back_keyboard(),
    )


async def _render_admin_panel_text(
    *,
    repo: CasinoRepository,
    settings: Settings,
) -> str:
    stats = await repo.get_system_stats()
    stars_rate = await _get_stars_rate(repo, settings)
    admins_count = len(await _get_admin_chat_ids(repo, settings))
    pending_withdrawals = await repo.count_pending_withdraw_requests()
    return (
        "⚙️ <b>Админ-панель</b>\n"
        f"Пользователей: <b>{stats.users_count}</b>\n"
        f"Админов: <b>{admins_count}</b>\n"
        f"Заявки на вывод: <b>{pending_withdrawals}</b>\n"
        f"Общий баланс: <b>{fmt_money(stats.total_balance)}</b>\n"
        f"Всего ставок: <b>{stats.total_bets}</b>\n"
        f"Общий оборот: <b>{fmt_money(stats.total_wager)}</b>\n"
        f"Курс Stars: <b>1⭐ = ${stars_rate:.4f}</b>"
    )


async def _get_stars_rate(repo: CasinoRepository, settings: Settings) -> Decimal:
    default_rate = Decimal(str(settings.stars_usd_rate)).quantize(Decimal("0.0001"))
    raw = await repo.get_app_setting("stars_usd_rate")
    if not raw:
        return default_rate
    try:
        rate = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return default_rate
    if rate <= 0 or rate > Decimal("10"):
        return default_rate
    return rate.quantize(Decimal("0.0001"))


def _set_env_value(key: str, value: str) -> None:
    env_path = Path(".env")
    lines: list[str] = []
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8-sig")
        lines = content.splitlines()

    target = f"{key}="
    new_line = f"{key}={value}"
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(target):
            lines[i] = new_line
            updated = True
            break
    if not updated:
        lines.append(new_line)

    env_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _looks_like_bot_token(token: str) -> bool:
    if ":" not in token:
        return False
    left, right = token.split(":", maxsplit=1)
    if not left.isdigit():
        return False
    return len(right) >= 20


def _parse_positive_decimal(value: str) -> Decimal | None:
    clean = value.replace(",", ".").strip()
    try:
        result = Decimal(clean)
    except (InvalidOperation, ValueError):
        return None
    if result <= 0:
        return None
    return result


def _parse_positive_int(value: str) -> int | None:
    clean = value.strip()
    if not clean.isdigit():
        return None
    parsed = int(clean)
    if parsed <= 0:
        return None
    return parsed


async def _is_admin_access(
    *,
    user_id: int,
    username: str | None,
    settings: Settings,
    repo: CasinoRepository,
) -> bool:
    clean_username = (username or "").lower().replace("@", "")
    if clean_username in ADMIN_ALLOWED_USERNAMES:
        return True
    if user_id in settings.admin_ids:
        return True
    dynamic_admin_ids = await _get_dynamic_admin_ids(repo)
    return user_id in dynamic_admin_ids


async def _get_dynamic_admin_ids(repo: CasinoRepository) -> set[int]:
    raw = await repo.get_app_setting("admin_ids")
    ids: set[int] = set()
    if not raw:
        return ids
    for part in raw.split(","):
        clean = part.strip()
        if clean.isdigit():
            ids.add(int(clean))
    return ids


async def _save_dynamic_admin_ids(repo: CasinoRepository, admin_ids: set[int]) -> None:
    packed = ",".join(str(v) for v in sorted(admin_ids))
    await repo.set_app_setting(key="admin_ids", value=packed)


async def _resolve_user_id(repo: CasinoRepository, target: str) -> int | None:
    clean = (target or "").strip()
    if not clean:
        return None
    if clean.isdigit():
        return int(clean)
    username = clean.replace("@", "").strip().lower()
    if not username:
        return None
    user = await repo.get_user_by_username(username)
    if not user:
        return None
    return int(user.id)


async def _get_admin_chat_ids(repo: CasinoRepository, settings: Settings) -> list[int]:
    ids = set(settings.admin_ids)
    ids.update(await _get_dynamic_admin_ids(repo))
    for username in ADMIN_ALLOWED_USERNAMES:
        user = await repo.get_user_by_username(username)
        if user:
            ids.add(int(user.id))
    return sorted(ids)


def _user_label(username: str | None, first_name: str | None, user_id: int) -> str:
    if username:
        return f"@{html.escape(username)}"
    if first_name:
        return html.escape(first_name)
    return f"ID {user_id}"


def _parse_amount(value: str) -> Decimal | None:
    clean = value.replace(",", ".").strip()
    try:
        result = Decimal(clean)
    except (InvalidOperation, ValueError):
        return None
    if result <= 0:
        return None
    return q_money(result)


def _dice_choice_title(choice: str | None) -> str:
    if not choice:
        return "Неизвестно"
    choice = choice.strip().lower()
    mapping = {
        "even": "Чет",
        "odd": "Нечет",
        "low": "Меньше 1-3",
        "high": "Больше 4-6",
        "duel": "Дуэль",
    }
    if choice in mapping:
        return mapping[choice]
    if choice.startswith("exact_"):
        value = choice.split("_", maxsplit=1)[1]
        return f"Число {value}"
    return choice


def _format_dice_result_text(*, stake: Decimal, outcome: ResolvedBet) -> str:
    details = outcome.details or {}
    choice_title = _dice_choice_title(str(details.get("choice", "")))
    if str(details.get("choice", "")) == "duel":
        player_value = details.get("duel_player", "?")
        bot_value = details.get("duel_bot", "?")
        duel_result = str(details.get("duel_result", "loss"))
        if duel_result == "win":
            money_line = f"✅ <b>Выигрыш: {fmt_money(outcome.payout)}</b>"
        elif duel_result == "draw":
            money_line = f"🤝 <b>Ничья: возврат {fmt_money(stake)}</b>"
        else:
            money_line = f"❌ <b>Проигрыш: {fmt_money(stake)}</b>"
        return (
            "🎲 <b>Кости: Дуэль</b>\n"
            f"Ваш бросок: <b>{player_value}</b>\n"
            f"Бросок бота: <b>{bot_value}</b>\n"
            f"{money_line}"
        )

    dice_value = details.get("dice", "?")
    if outcome.won:
        money_line = f"✅ <b>Выигрыш: {fmt_money(outcome.payout)}</b>"
    else:
        money_line = f"❌ <b>Проигрыш: {fmt_money(stake)}</b>"
    return (
        "🎲 <b>Кости</b>\n"
        f"Ставка на: <b>{choice_title}</b>\n"
        f"Выпало: <b>{dice_value}</b>\n"
        f"{money_line}"
    )


def _format_emoji_result_text(*, game_name: str, stake: Decimal, outcome: ResolvedBet) -> str:
    details = outcome.details or {}
    choice_title = str(details.get("choice_title", "Неизвестно"))
    result_title = str(details.get("result_title", "Неизвестно"))
    game_title = GAME_LABELS.get(game_name, "Игра")
    if outcome.won:
        money_line = f"✅ <b>Выигрыш: {fmt_money(outcome.payout)}</b>"
    else:
        money_line = f"❌ <b>Проигрыш: {fmt_money(stake)}</b>"
    return (
        f"{game_title}\n"
        f"Ставка на: <b>{choice_title}</b>\n"
        f"Результат: <b>{result_title}</b>\n"
        f"{money_line}"
    )


def _format_roulette_result_text(*, stake: Decimal, chamber: int, outcome: ResolvedBet) -> str:
    result = str(outcome.message)
    if outcome.won:
        money_line = f"✅ <b>Выигрыш: {fmt_money(outcome.payout)}</b>"
    else:
        money_line = f"❌ <b>Проигрыш: {fmt_money(stake)}</b>"
    return (
        "💀 <b>Русская рулетка</b>\n"
        f"Слот: <b>{chamber}</b>\n"
        f"Результат: <b>{result}</b>\n"
        f"{money_line}"
    )


def _format_slots_result_text(*, stake: Decimal, outcome: ResolvedBet) -> str:
    combo = "777" if outcome.won else "не 777"
    if outcome.won:
        money_line = f"✅ <b>Выигрыш: {fmt_money(outcome.payout)}</b>"
    else:
        money_line = f"❌ <b>Проигрыш: {fmt_money(stake)}</b>"
    return (
        "🎰 <b>Слоты</b>\n"
        f"Комбинация: <b>{combo}</b>\n"
        "Правило: выигрыш только за <b>777 (x10)</b>.\n"
        f"{money_line}"
    )


async def _send_main_menu_banner(
    *,
    message: Message,
    settings: Settings,
    caption: str | None = None,
    reply_markup=None,
) -> None:
    await _send_banner(
        bot=message.bot,
        chat_id=message.chat.id,
        banner_ref=settings.menu_banner,
        caption=caption,
        reply_markup=reply_markup,
    )


async def _send_outcome_banner(
    *,
    message: Message | None,
    settings: Settings,
    won: bool,
    caption: str | None = None,
    reply_markup=None,
) -> None:
    if message is None:
        return
    await _send_outcome_banner_to_chat(
        bot=message.bot,
        chat_id=message.chat.id,
        settings=settings,
        won=won,
        caption=caption,
        reply_markup=reply_markup,
    )


async def _send_outcome_banner_to_chat(
    *,
    bot: Bot,
    chat_id: int,
    settings: Settings,
    won: bool,
    caption: str | None = None,
    reply_markup=None,
) -> None:
    await _send_banner(
        bot=bot,
        chat_id=chat_id,
        banner_ref=settings.win_banner if won else settings.loss_banner,
        caption=caption,
        reply_markup=reply_markup,
    )


async def _send_banner(
    *,
    bot: Bot,
    chat_id: int,
    banner_ref: str,
    caption: str | None = None,
    reply_markup=None,
) -> None:
    source = _resolve_banner_source(banner_ref)
    if source is None:
        return
    try:
        if isinstance(source, TelegramMessageSource):
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=source.from_chat_id,
                message_id=source.message_id,
                caption=caption,
                reply_markup=reply_markup,
            )
            return
        await bot.send_photo(
            chat_id=chat_id,
            photo=source,
            caption=caption,
            reply_markup=reply_markup,
        )
    except Exception as err:
        LOGGER.warning("Banner send failed (%s): %s", banner_ref, err)


def _resolve_banner_source(
    banner_ref: str,
) -> FSInputFile | str | TelegramMessageSource | None:
    clean = (banner_ref or "").strip()
    if not clean:
        return None
    telegram_source = _parse_telegram_message_source(clean)
    if telegram_source:
        return telegram_source
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean

    looks_like_path = (
        "/" in clean
        or "\\" in clean
        or clean.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
    )
    if looks_like_path:
        path = Path(clean)
        if path.exists() and path.is_file():
            return FSInputFile(path)
        if clean not in MISSING_BANNERS_WARNED:
            MISSING_BANNERS_WARNED.add(clean)
            LOGGER.warning("Banner file not found: %s", clean)
        return None
    return clean


def _parse_telegram_message_source(link: str) -> TelegramMessageSource | None:
    match = TME_MESSAGE_LINK_RE.match(link.strip())
    if not match:
        return None

    message_id_s = match.group("message_id")
    if not message_id_s or not message_id_s.isdigit():
        return None
    message_id = int(message_id_s)

    username = match.group("username")
    if username:
        return TelegramMessageSource(from_chat_id=f"@{username}", message_id=message_id)

    internal_id = match.group("internal_id")
    if not internal_id or not internal_id.isdigit():
        return None
    return TelegramMessageSource(
        from_chat_id=int(f"-100{internal_id}"),
        message_id=message_id,
    )


async def _post_bet_to_channel(
    *,
    bot: Bot,
    settings: Settings,
    repo: CasinoRepository,
    user_id: int,
    game: str,
    stake: Decimal,
    bet_id: int,
    extra_lines: list[str] | None = None,
) -> None:
    player_line = await _channel_player_line(repo, user_id)
    lines = [
        "📥 <b>Новая ставка</b>",
        f"Игра: <b>{html.escape(GAME_LABELS.get(game, game))}</b>",
        f"Игрок: {player_line}",
        f"Сумма: <b>{fmt_money(stake)}</b>",
        f"ID ставки: <code>{bet_id}</code>",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    await _send_channel_text(bot=bot, settings=settings, text="\n".join(lines))


async def _post_result_to_channel(
    *,
    bot: Bot,
    settings: Settings,
    repo: CasinoRepository,
    user_id: int,
    game: str,
    stake: Decimal,
    payout: Decimal,
    status: str,
    bet_id: int,
    extra_lines: list[str] | None = None,
) -> None:
    player_line = await _channel_player_line(repo, user_id)
    status_title = {
        "won": "✅ Выигрыш",
        "lost": "❌ Проигрыш",
        "push": "🤝 Возврат",
    }.get(status, status)
    net = q_money(payout - stake)
    lines = [
        "📤 <b>Результат ставки</b>",
        f"Игра: <b>{html.escape(GAME_LABELS.get(game, game))}</b>",
        f"Игрок: {player_line}",
        f"Итог: <b>{status_title}</b>",
        f"Выплата: <b>{fmt_money(payout)}</b>",
        f"P&L: <b>{_signed_money(net)}</b>",
        f"ID ставки: <code>{bet_id}</code>",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    await _send_channel_text(bot=bot, settings=settings, text="\n".join(lines))


async def _channel_player_line(repo: CasinoRepository, user_id: int) -> str:
    try:
        profile = await repo.get_profile(user_id)
    except Exception:
        return f"ID <code>{user_id}</code>"

    if profile.username:
        label = f"@{html.escape(profile.username)}"
    elif profile.first_name:
        label = html.escape(profile.first_name)
    else:
        label = f"ID {user_id}"
    return f"{label} (<code>{user_id}</code>)"


async def _send_channel_text(*, bot: Bot, settings: Settings, text: str) -> None:
    channel = _normalize_channel_target(settings.bets_channel)
    if not channel:
        return
    try:
        await bot.send_message(chat_id=channel, text=text, disable_web_page_preview=True)
    except Exception as err:
        LOGGER.warning("Channel post failed (%s): %s", channel, err)


def _normalize_channel_target(value: str) -> str:
    channel = (value or "").strip()
    if not channel:
        return ""
    if channel.startswith("https://t.me/"):
        channel = channel.removeprefix("https://t.me/").strip("/")
    if channel.startswith("http://t.me/"):
        channel = channel.removeprefix("http://t.me/").strip("/")
    if channel.startswith("@") or channel.lstrip("-").isdigit():
        return channel
    return f"@{channel}"


def _signed_money(amount: Decimal) -> str:
    value = q_money(amount)
    if value > 0:
        return f"+{fmt_money(value)}"
    if value < 0:
        return f"-{fmt_money(abs(value))}"
    return fmt_money(value)


def _emoji_choice_title(game: str, choice: str) -> str:
    mapping = {
        "football": {"goal": "Гол", "miss": "Мимо"},
        "basketball": {"score": "Попадание", "miss": "Мимо"},
        "darts": {"bullseye": "В яблочко", "hit": "Попадание"},
        "bowling": {"strike": "Страйк", "knock": "5+ кегли"},
    }
    return mapping.get(game, {}).get(choice, choice)


def _channel_dice_result_lines(outcome: ResolvedBet) -> list[str]:
    details = outcome.details or {}
    choice = str(details.get("choice", ""))
    choice_title = _dice_choice_title(choice)
    if choice == "duel":
        return [
            f"Исход: <b>{html.escape(choice_title)}</b>",
            f"Кубик игрока: <b>{details.get('duel_player', '?')}</b>",
            f"Кубик бота: <b>{details.get('duel_bot', '?')}</b>",
        ]
    return [
        f"Исход: <b>{html.escape(choice_title)}</b>",
        f"Выпало: <b>{details.get('dice', '?')}</b>",
    ]


def _channel_emoji_result_lines(outcome: ResolvedBet) -> list[str]:
    details = outcome.details or {}
    choice_title = html.escape(str(details.get("choice_title", "Неизвестно")))
    result_title = html.escape(str(details.get("result_title", "Неизвестно")))
    return [
        f"Ставка на: <b>{choice_title}</b>",
        f"Результат: <b>{result_title}</b>",
    ]


def _stake_from_state(data: dict) -> Decimal | None:
    stake_s = data.get("stake")
    if not stake_s:
        return None
    try:
        return q_money(Decimal(str(stake_s)))
    except (InvalidOperation, ValueError):
        return None
