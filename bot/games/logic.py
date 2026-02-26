from __future__ import annotations

import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.utils import q_money, to_decimal

EXPLOSION = "\U0001F4A5"
EMPTY_CELL = "\u25AB"
JACKPOT_SLOT_VALUE = 64


@dataclass(slots=True)
class ResolvedBet:
    won: bool
    payout: Decimal
    base_multiplier: Decimal
    applied_edge: Decimal
    message: str
    details: dict[str, Any]


def effective_multiplier_with_edge(base_multiplier: Decimal, edge: float) -> Decimal:
    base_mult = to_decimal(base_multiplier)
    edge_dec = to_decimal(edge)
    if edge_dec < 0:
        edge_dec = Decimal("0")
    if edge_dec > Decimal("0.99"):
        edge_dec = Decimal("0.99")

    profit_part = (base_mult - Decimal("1")) * (Decimal("1") - edge_dec)
    effective = Decimal("1") + profit_part
    if effective < Decimal("1"):
        effective = Decimal("1")
    return effective.quantize(Decimal("0.01"))


def payout_with_edge(stake: Decimal, base_multiplier: Decimal, edge: float) -> Decimal:
    stake_dec = to_decimal(stake)
    effective_multiplier = effective_multiplier_with_edge(base_multiplier, edge)
    payout = stake_dec * effective_multiplier
    return q_money(payout)


def payout_exact(stake: Decimal, multiplier: Decimal) -> Decimal:
    return q_money(to_decimal(stake) * to_decimal(multiplier))


def spin_slots(stake: Decimal, edge: float, slot_value: int | None = None) -> ResolvedBet:
    # Логика синхронизирована с анимацией Telegram: только джекпот (777) платит x10.
    won = slot_value == JACKPOT_SLOT_VALUE
    base_multiplier = Decimal("10.0") if won else Decimal("0")
    payout = q_money(stake * base_multiplier) if won else Decimal("0")

    if won:
        msg = "7️⃣ 7️⃣ 7️⃣\nДжекпот! Выплата x10."
    else:
        msg = "Комбинация не сыграла.\nВыигрывает только 777 (x10)."

    return ResolvedBet(
        won=won,
        payout=payout,
        base_multiplier=base_multiplier,
        applied_edge=Decimal(str(edge)),
        message=msg,
        details={"slot_value": slot_value, "jackpot_value": JACKPOT_SLOT_VALUE},
    )


def resolve_dice(stake: Decimal, edge: float, dice_value: int, choice: str) -> ResolvedBet:
    choice = choice.strip().lower()
    base_multiplier = Decimal("0")

    won = False
    if choice == "even":
        won = dice_value % 2 == 0
        base_multiplier = Decimal("1.7")
    elif choice == "odd":
        won = dice_value % 2 == 1
        base_multiplier = Decimal("1.7")
    elif choice == "low":
        won = dice_value <= 3
        base_multiplier = Decimal("1.7")
    elif choice == "high":
        won = dice_value >= 4
        base_multiplier = Decimal("1.7")
    elif choice.startswith("exact_"):
        value = int(choice.split("_", maxsplit=1)[1])
        won = dice_value == value
        base_multiplier = Decimal("4.0")
    else:
        raise ValueError("Неверный исход для костей")

    payout = payout_exact(stake, base_multiplier) if won else Decimal("0")

    return ResolvedBet(
        won=won,
        payout=payout,
        base_multiplier=base_multiplier,
        applied_edge=Decimal("0"),
        message=f"Выпало значение кубика: {dice_value}",
        details={"dice": dice_value, "choice": choice},
    )


def resolve_dice_duel(
    *,
    stake: Decimal,
    edge: float,
    player_value: int,
    bot_value: int,
) -> ResolvedBet:
    if player_value < 1 or player_value > 6 or bot_value < 1 or bot_value > 6:
        raise ValueError("Некорректные значения кубиков")

    base_multiplier = Decimal("1.8")
    duel_result = "draw"
    won = False
    payout = stake
    if player_value > bot_value:
        duel_result = "win"
        won = True
        payout = payout_exact(stake, base_multiplier)
    elif player_value < bot_value:
        duel_result = "loss"
        payout = Decimal("0")

    return ResolvedBet(
        won=won,
        payout=q_money(payout),
        base_multiplier=base_multiplier,
        applied_edge=Decimal("0"),
        message="Дуэль",
        details={
            "choice": "duel",
            "duel_player": player_value,
            "duel_bot": bot_value,
            "duel_result": duel_result,
        },
    )


def resolve_emoji_game(
    *,
    stake: Decimal,
    edge: float,
    game: str,
    dice_value: int,
    choice: str,
) -> ResolvedBet:
    game = game.strip().lower()
    choice = choice.strip().lower()
    result_title = ""

    if game == "football":
        # Telegram football animation has goal outcomes for values 3..5.
        goal = dice_value in {3, 4, 5}
        result_title = "Гол" if goal else "Мимо"
        outcomes = {
            "goal": (goal, Decimal("1.4"), "Гол"),
            "miss": (not goal, Decimal("1.8"), "Мимо"),
        }
        title = "⚽ Футбол"
    elif game == "basketball":
        score = dice_value in {4, 5}
        result_title = "Попадание" if score else "Мимо"
        outcomes = {
            "score": (score, Decimal("1.8"), "Попадание"),
            "miss": (not score, Decimal("1.4"), "Мимо"),
        }
        title = "🏀 Баскетбол"
    elif game == "darts":
        bullseye = dice_value == 6
        miss = dice_value == 1
        if bullseye:
            result_title = "В яблочко"
        elif miss:
            result_title = "Мимо"
        else:
            result_title = "Остальное"
        outcomes = {
            "bullseye": (bullseye, Decimal("5.0"), "В яблочко"),
            "miss": (miss, Decimal("5.0"), "Мимо"),
            "hit": (miss, Decimal("5.0"), "Мимо"),
        }
        title = "🎯 Дартс"
    elif game == "bowling":
        strike = dice_value == 6
        miss = dice_value == 1
        if strike:
            result_title = "Страйк"
        elif miss:
            result_title = "Мимо"
        else:
            result_title = "Остальное"
        outcomes = {
            "strike": (strike, Decimal("5.0"), "Страйк"),
            "miss": (miss, Decimal("5.0"), "Мимо"),
            "knock": (miss, Decimal("5.0"), "Мимо"),
        }
        title = "🎳 Боулинг"
    else:
        raise ValueError("Неверная эмодзи-игра")

    if choice not in outcomes:
        raise ValueError("Неверный исход для эмодзи-игры")

    won, base_multiplier, choice_title = outcomes[choice]
    payout = payout_exact(stake, base_multiplier) if won else Decimal("0")

    return ResolvedBet(
        won=won,
        payout=payout,
        base_multiplier=base_multiplier,
        applied_edge=Decimal("0"),
        message=f"{title}\nИсход: {choice_title}\nРезультат: {result_title}",
        details={
            "game": game,
            "choice": choice,
            "choice_title": choice_title,
            "result_title": result_title,
            "dice": dice_value,
        },
    )


def resolve_crash(stake: Decimal, edge: float, target_multiplier: Decimal) -> ResolvedBet:
    # Heavy-tail кривая: низкие множители встречаются чаще высоких.
    crash_point = float(generate_crash_point())

    won = float(target_multiplier) <= crash_point
    payout = (
        payout_with_edge(stake, to_decimal(target_multiplier), edge)
        if won
        else Decimal("0")
    )

    return ResolvedBet(
        won=won,
        payout=payout,
        base_multiplier=to_decimal(target_multiplier),
        applied_edge=Decimal(str(edge)),
        message=f"Ракета взорвалась на x{crash_point}",
        details={
            "target_multiplier": float(target_multiplier),
            "crash_point": crash_point,
        },
    )


def generate_crash_point() -> Decimal:
    r = random.random()
    if r < 0.18:
        value = random.uniform(1.01, 1.05)
    elif r < 0.45:
        value = random.uniform(1.06, 1.20)
    elif r < 0.75:
        value = random.uniform(1.21, 1.80)
    elif r < 0.91:
        value = random.uniform(1.81, 3.00)
    elif r < 0.98:
        value = random.uniform(3.01, 4.80)
    else:
        value = random.uniform(4.81, 8.00)
    return to_decimal(round(value, 2))


def next_crash_multiplier(current: Decimal) -> Decimal:
    current_dec = to_decimal(current)
    base_step = Decimal(str(random.uniform(0.01, 0.05)))
    accel = min(
        Decimal("0.04"),
        max(Decimal("0"), (current_dec - Decimal("1.00")) * Decimal("0.015")),
    )
    nxt = (current_dec + base_step + accel).quantize(Decimal("0.01"))
    if nxt < Decimal("1.01"):
        return Decimal("1.01")
    return nxt


def resolve_roulette(stake: Decimal, edge: float, chosen_chamber: int) -> ResolvedBet:
    if chosen_chamber < 1 or chosen_chamber > 6:
        raise ValueError("Неверный слот револьвера")

    # Под house-edge риск выше классического: 4 пули из 6.
    bullets = random.sample([1, 2, 3, 4, 5, 6], k=4)
    hit_bullet = chosen_chamber in bullets

    base_multiplier = Decimal("1.7")
    payout = payout_with_edge(stake, base_multiplier, edge) if not hit_bullet else Decimal("0")

    return ResolvedBet(
        won=not hit_bullet,
        payout=payout,
        base_multiplier=base_multiplier,
        applied_edge=Decimal(str(edge)),
        message="Пуля" if hit_bullet else "Пусто",
        details={"chosen_chamber": chosen_chamber, "bullets": bullets},
    )


def create_mines_state(mines_count: int) -> dict[str, Any]:
    if mines_count <= 0 or mines_count >= 25:
        raise ValueError("Некорректное количество мин")

    cells = list(range(25))
    mine_cells = random.sample(cells, k=mines_count)
    return {
        "grid_size": 25,
        "mines_count": mines_count,
        "mine_cells": mine_cells,
        "opened_cells": [],
        "current_multiplier": "1.00",
    }


def open_mines_cell(
    *,
    state: dict[str, Any],
    cell_index: int,
    edge: float,
) -> tuple[bool, Decimal, dict[str, Any]]:
    if cell_index < 0 or cell_index >= 25:
        raise ValueError("Некорректная ячейка")

    mine_cells = set(state["mine_cells"])
    opened_cells = set(state["opened_cells"])
    mines_count = int(state["mines_count"])

    if cell_index in opened_cells:
        current_multiplier = to_decimal(state["current_multiplier"])
        return False, current_multiplier, state

    if cell_index in mine_cells:
        opened_cells.add(cell_index)
        state["opened_cells"] = sorted(opened_cells)
        return True, Decimal("0"), state

    opened_cells.add(cell_index)
    safe_picks = len(opened_cells)

    fair_multiplier = Decimal(math.comb(25, safe_picks)) / Decimal(
        math.comb(25 - mines_count, safe_picks)
    )
    edge_dec = Decimal(str(edge))
    extra_edge = Decimal("0")
    if mines_count <= 3:
        extra_edge = Decimal("0.17")
    elif mines_count <= 5:
        extra_edge = Decimal("0.10")
    elif mines_count <= 7:
        extra_edge = Decimal("0.05")

    total_edge = edge_dec + extra_edge
    if total_edge > Decimal("0.95"):
        total_edge = Decimal("0.95")

    adjusted = fair_multiplier * (Decimal("1") - total_edge)
    if adjusted < Decimal("1.00"):
        adjusted = Decimal("1.00")
    current_multiplier = adjusted.quantize(Decimal("0.01"))

    state["opened_cells"] = sorted(opened_cells)
    state["current_multiplier"] = str(current_multiplier)
    return False, current_multiplier, state


def mines_cashout(stake: Decimal, current_multiplier: Decimal) -> Decimal:
    return q_money(stake * to_decimal(current_multiplier))
