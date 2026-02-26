from __future__ import annotations

from decimal import Decimal

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.utils import q_money

MINE = "\U0001F4A3"
SAFE = "\u2705"
HIDDEN = "\u2B1C"
BLANK = "\u25AB"
GIFT = "\U0001F381"

MENU_PLAY = "\U0001F3B2 Играть"
MENU_PROFILE = "\U0001F464 Профиль"
MENU_REF = "\U0001F381 Реф. программа"
MENU_DEPOSIT = "\U0001F4B3 Пополнить"
MENU_WITHDRAW = "\U0001F4B8 Вывести"
MENU_BALANCE = "\U0001F4B0 Баланс"
MENU_SUPPORT = "\U0001F6DF Техподдержка"


def _fmt_money(amount: Decimal) -> str:
    return f"${q_money(amount)}"


def _ibutton(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    kwargs: dict[str, str] = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    # style intentionally ignored: not supported on all Bot API versions/chats
    return InlineKeyboardButton(**kwargs)


def _rbutton(text: str, *, style: str | None = None) -> KeyboardButton:
    kwargs: dict[str, str] = {"text": text}
    # style intentionally ignored: not supported on all Bot API versions/chats
    return KeyboardButton(**kwargs)


def back_keyboard(
    *,
    callback_data: str = "open_main_menu",
    text: str = "\u2B05\uFE0F Назад",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_ibutton(text, callback_data=callback_data)]])


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [_rbutton(MENU_PLAY, style="success"), _rbutton(MENU_PROFILE)],
            [_rbutton(MENU_BALANCE), _rbutton(MENU_REF)],
            [_rbutton(MENU_SUPPORT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие в меню",
    )


def main_menu_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton(MENU_PLAY, callback_data="show_games"),
                _ibutton(MENU_PROFILE, callback_data="menu_profile"),
            ],
            [
                _ibutton(MENU_BALANCE, callback_data="menu_balance"),
                _ibutton(MENU_REF, callback_data="menu_ref"),
            ],
            [_ibutton(MENU_SUPPORT, callback_data="menu_support")],
        ]
    )


def games_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton("\U0001F3B2 Кости", callback_data="select_game:dice"),
                _ibutton("\u26BD Футбол", callback_data="select_game:football"),
                _ibutton("\U0001F3C0 Баскетбол", callback_data="select_game:basketball"),
            ],
            [
                _ibutton("\U0001F3B0 Слоты", callback_data="select_game:slots"),
                _ibutton("\U0001F4A3 Мины", callback_data="select_game:mines"),
                _ibutton("\U0001F680 Краш", callback_data="select_game:crash"),
            ],
            [_ibutton("\U0001F480 Рулетка", callback_data="select_game:roulette")],
            [_ibutton("\u2B05\uFE0F Назад", callback_data="open_main_menu")],
        ]
    )


def stake_amount_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton("$1", callback_data="stake_preset:1"),
                _ibutton("$5", callback_data="stake_preset:5"),
                _ibutton("$10", callback_data="stake_preset:10"),
            ],
            [
                _ibutton("$25", callback_data="stake_preset:25"),
                _ibutton("$50", callback_data="stake_preset:50"),
                _ibutton("$100", callback_data="stake_preset:100"),
            ],
            [_ibutton("\u270D\uFE0F Ввести сумму вручную", callback_data="stake_manual")],
            [_ibutton("\u2B05\uFE0F Назад к играм", callback_data="show_games")],
        ]
    )


def replay_keyboard(*, game: str, stake: Decimal, choice: str | None = None) -> InlineKeyboardMarkup:
    stake_value = q_money(stake)
    choice_value = choice or "_"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton(
                    "\U0001F501 Повторить ставку",
                    callback_data=f"repeat_bet:{game}:{stake_value}:{choice_value}",
                    style="success",
                ),
                _ibutton("\U0001F3AE Еще игра", callback_data="show_games"),
            ],
            [_ibutton("\U0001F4B3 Пополнить", callback_data="quick_deposit")],
            [_ibutton("\u2B05\uFE0F Назад", callback_data="open_main_menu")],
        ]
    )


def profile_actions_keyboard(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            _ibutton("\U0001F4B3 Пополнить", callback_data="quick_deposit", style="success"),
            _ibutton("\U0001F4B8 Вывести", callback_data="quick_withdraw"),
        ],
        [_ibutton("\u2B05\uFE0F Назад", callback_data="open_main_menu")],
    ]
    if is_admin:
        rows.append([_ibutton("⚙️ Админ-панель", callback_data="admin:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton("\U0001F4CA Статистика", callback_data="admin:stats"),
                _ibutton("\U0001F465 Пользователи", callback_data="admin:users:0"),
            ],
            [_ibutton("📤 Запросы на вывод", callback_data="admin:withdrawals:0")],
            [
                _ibutton("\U0001F4B1 Курс Stars", callback_data="admin:set_stars_rate"),
                _ibutton("\U0001F510 BOT_TOKEN", callback_data="admin:set_bot_token"),
            ],
            [
                _ibutton("👮 Добавить админа", callback_data="admin:add_admin"),
                _ibutton("💸 Выдать баланс", callback_data="admin:grant"),
            ],
            [
                _ibutton("\U0001F4E3 Рассылка", callback_data="admin:broadcast"),
                _ibutton("\U0001F6D1 Стоп бота", callback_data="admin:stop"),
            ],
            [_ibutton("\U0001F504 Обновить", callback_data="admin:refresh")],
        ]
    )


def admin_users_keyboard(*, offset: int, total: int, limit: int = 20) -> InlineKeyboardMarkup:
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit
    rows: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_row.append(_ibutton("⬅️ Назад", callback_data=f"admin:users:{prev_offset}"))
    if next_offset < total:
        nav_row.append(_ibutton("Вперед ➡️", callback_data=f"admin:users:{next_offset}"))
    if nav_row:
        rows.append(nav_row)
    rows.append([_ibutton("⬅️ В админку", callback_data="admin:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_withdrawals_keyboard(
    *,
    request_ids: list[int],
    offset: int,
    total: int,
    limit: int = 10,
) -> InlineKeyboardMarkup:
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit

    rows: list[list[InlineKeyboardButton]] = []
    for request_id in request_ids:
        rows.append([_ibutton(f"✅ Подтвердить #{request_id}", callback_data=f"admin:withdraw_confirm:{request_id}")])

    nav_row: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_row.append(_ibutton("⬅️ Назад", callback_data=f"admin:withdrawals:{prev_offset}"))
    if next_offset < total:
        nav_row.append(_ibutton("Вперед ➡️", callback_data=f"admin:withdrawals:{next_offset}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([_ibutton("⬅️ В админку", callback_data="admin:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_withdraw_request_alert_keyboard(*, request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ibutton(f"✅ Подтвердить #{request_id}", callback_data=f"admin:withdraw_confirm:{request_id}")],
            [_ibutton("📤 Все запросы", callback_data="admin:withdrawals:0")],
        ]
    )


def dice_choice_keyboard(edge: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton("Чет x1.7", callback_data="dice_choice:even"),
                _ibutton("Нечет x1.7", callback_data="dice_choice:odd"),
            ],
            [
                _ibutton("Меньше 1-3 x1.7", callback_data="dice_choice:low"),
                _ibutton("Больше 4-6 x1.7", callback_data="dice_choice:high"),
            ],
            [
                _ibutton("1 x4", callback_data="dice_choice:exact_1"),
                _ibutton("2 x4", callback_data="dice_choice:exact_2"),
                _ibutton("3 x4", callback_data="dice_choice:exact_3"),
            ],
            [
                _ibutton("4 x4", callback_data="dice_choice:exact_4"),
                _ibutton("5 x4", callback_data="dice_choice:exact_5"),
                _ibutton("6 x4", callback_data="dice_choice:exact_6"),
            ],
            [_ibutton("⚔️ Дуэль x1.8", callback_data="dice_choice:duel")],
            [_ibutton("\u2B05\uFE0F Назад к играм", callback_data="show_games")],
        ]
    )


def emoji_game_choice_keyboard(game: str, edge: float) -> InlineKeyboardMarkup:
    choices: dict[str, list[tuple[str, str]]] = {
        "football": [
            ("\u26BD Гол x1.4", "goal"),
            ("\u274C Мимо x1.8", "miss"),
        ],
        "basketball": [
            ("\U0001F3C0 Попадание x1.8", "score"),
            ("\u274C Мимо x1.4", "miss"),
        ],
        "darts": [
            ("\U0001F3AF В яблочко x5", "bullseye"),
            ("\u274C Мимо x5", "miss"),
        ],
        "bowling": [
            ("\U0001F3B3 Страйк x5", "strike"),
            ("\u274C Мимо x5", "miss"),
        ],
    }
    rows: list[list[InlineKeyboardButton]] = []
    for title, choice in choices.get(game, []):
        rows.append([_ibutton(title, callback_data=f"emoji_choice:{game}:{choice}")])

    rows.append([_ibutton("\u2B05\uFE0F Назад к играм", callback_data="show_games")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def roulette_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            _ibutton("1", callback_data="roulette_choice:1"),
            _ibutton("2", callback_data="roulette_choice:2"),
            _ibutton("3", callback_data="roulette_choice:3"),
        ],
        [
            _ibutton("4", callback_data="roulette_choice:4"),
            _ibutton("5", callback_data="roulette_choice:5"),
            _ibutton("6", callback_data="roulette_choice:6"),
        ],
        [_ibutton("\u2B05\uFE0F Назад к играм", callback_data="show_games")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crash_cashout_keyboard(session_id: str, cashout_multiplier: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton(
                    f"\U0001F381 Забрать x{cashout_multiplier}",
                    callback_data=f"crash_cashout:{session_id}",
                    style="success",
                )
            ]
        ]
    )


def mines_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton("3 \U0001F4A3", callback_data="mines_count:3"),
                _ibutton("5 \U0001F4A3", callback_data="mines_count:5"),
            ],
            [
                _ibutton("7 \U0001F4A3", callback_data="mines_count:7"),
                _ibutton("10 \U0001F4A3", callback_data="mines_count:10"),
            ],
            [
                _ibutton("12 \U0001F4A3", callback_data="mines_count:12"),
                _ibutton("15 \U0001F4A3", callback_data="mines_count:15"),
            ],
            [_ibutton("\u2B05\uFE0F Назад к играм", callback_data="show_games")],
        ]
    )


def mines_grid_keyboard(
    *,
    session_id: str,
    state: dict,
    reveal_all: bool = False,
    interactive: bool = True,
) -> InlineKeyboardMarkup:
    opened = set(state.get("opened_cells", []))
    mines = set(state.get("mine_cells", []))

    rows: list[list[InlineKeyboardButton]] = []

    for r in range(5):
        row: list[InlineKeyboardButton] = []
        for c in range(5):
            idx = r * 5 + c
            if idx in opened:
                text = MINE if idx in mines else SAFE
                row.append(_ibutton(text, callback_data="noop"))
            elif reveal_all and idx in mines:
                row.append(_ibutton(MINE, callback_data="noop"))
            elif not interactive:
                row.append(_ibutton(BLANK, callback_data="noop"))
            else:
                row.append(_ibutton(HIDDEN, callback_data=f"mines_open:{session_id}:{idx}"))
        rows.append(row)

    if interactive:
        current_multiplier = state.get("current_multiplier", "1.00")
        rows.append(
            [
                _ibutton(
                    f"{GIFT} Забрать x{current_multiplier}",
                    callback_data=f"mines_cashout:{session_id}",
                    style="success",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def deposit_crypto_asset_keyboard(
    *,
    assets: list[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for asset in assets:
        row.append(_ibutton(asset, callback_data=f"deposit_crypto_asset:{asset}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_ibutton("\u2B05\uFE0F Назад", callback_data="quick_deposit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deposit_crypto_amount_keyboard(asset: str) -> InlineKeyboardMarkup:
    asset_clean = asset.upper()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton(f"1 {asset_clean}", callback_data=f"deposit_amount:{asset_clean}:1"),
                _ibutton(f"5 {asset_clean}", callback_data=f"deposit_amount:{asset_clean}:5"),
                _ibutton(f"10 {asset_clean}", callback_data=f"deposit_amount:{asset_clean}:10"),
            ],
            [
                _ibutton(f"25 {asset_clean}", callback_data=f"deposit_amount:{asset_clean}:25"),
                _ibutton(f"50 {asset_clean}", callback_data=f"deposit_amount:{asset_clean}:50"),
                _ibutton(f"100 {asset_clean}", callback_data=f"deposit_amount:{asset_clean}:100"),
            ],
            [_ibutton("✍️ Своя сумма", callback_data=f"deposit_crypto_manual:{asset_clean}")],
            [_ibutton("\u2B05\uFE0F Назад к монетам", callback_data="deposit_method:crypto")],
        ]
    )


def deposit_method_keyboard(
    *,
    crypto_enabled: bool,
    stars_enabled: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            _ibutton(
                "\U0001F7E2 CryptoBot (USDT/TON)"
                if crypto_enabled
                else "\u26AA CryptoBot (недоступно)",
                callback_data="deposit_method:crypto",
                style="success" if crypto_enabled else None,
            )
        ]
    ]
    if stars_enabled:
        rows.append(
            [
                _ibutton(
                    "\u2B50 Telegram Stars",
                    callback_data="deposit_method:stars",
                    style="warning",
                )
            ]
        )
    else:
        rows.append([_ibutton("Telegram Stars (недоступно)", callback_data="noop")])

    rows.append([_ibutton("\u2B05\uFE0F Назад", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stars_amount_keyboard(rate: float) -> InlineKeyboardMarkup:
    rate_dec = Decimal(str(rate))

    def stars_label(stars: int) -> str:
        usd = Decimal(stars) * rate_dec
        return f"\u2B50 {stars} ({_fmt_money(usd)})"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibutton(stars_label(50), callback_data="deposit_stars:50"),
                _ibutton(stars_label(100), callback_data="deposit_stars:100"),
            ],
            [
                _ibutton(stars_label(250), callback_data="deposit_stars:250"),
                _ibutton(stars_label(500), callback_data="deposit_stars:500"),
            ],
            [_ibutton("✍️ Своя сумма ⭐", callback_data="deposit_stars_manual", style="warning")],
            [_ibutton("\u2B05\uFE0F Назад", callback_data="quick_deposit")],
        ]
    )


def invoice_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ibutton("\U0001F7E2 Оплатить счет", url=pay_url, style="success")],
            [_ibutton("Проверить оплату", callback_data="deposit_check")],
            [_ibutton("\u2B05\uFE0F Назад", callback_data="quick_deposit")],
        ]
    )
