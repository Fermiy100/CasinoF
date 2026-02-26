from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from aiogram.types import User as TgUser
from sqlalchemy import Select, desc, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.config import Settings
from bot.db.models import AppSetting, Base, Bet, GameSession, Invoice, Transaction, User
from bot.utils import q_money


class BalanceError(Exception):
    pass


class NotFoundError(Exception):
    pass


@dataclass(slots=True)
class ProfileSummary:
    user_id: int
    balance: Decimal
    total_bets: int
    total_wager: Decimal
    invited_count: int
    referral_earnings: Decimal
    username: str | None
    first_name: str | None


@dataclass(slots=True)
class SystemStats:
    users_count: int
    total_balance: Decimal
    total_bets: int
    total_wager: Decimal


@dataclass(slots=True)
class UserBalanceRow:
    user_id: int
    username: str | None
    first_name: str | None
    balance: Decimal
    total_bets: int
    total_wager: Decimal


@dataclass(slots=True)
class WithdrawRequestRow:
    request_id: int
    user_id: int
    username: str | None
    first_name: str | None
    amount: Decimal
    status: str
    created_at: datetime
    details: dict[str, Any]


@dataclass(slots=True)
class WithdrawApproveResult:
    request_id: int
    user_id: int
    amount: Decimal


class CasinoRepository:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self._session_factory = session_factory
        self._referral_rate = Decimal(str(settings.referral_rate))

    async def init_db(self, engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def ensure_user(self, tg_user: TgUser, start_payload: str | None = None) -> User:
        referral_id = self._parse_referral(start_payload)

        async with self._session_factory() as session:
            async with session.begin():
                user = await session.get(User, tg_user.id)
                if user:
                    user.username = tg_user.username
                    user.first_name = tg_user.first_name
                    return user

                referred_by: int | None = None
                if referral_id and referral_id != tg_user.id:
                    referrer = await session.get(User, referral_id)
                    if referrer:
                        referred_by = referrer.id

                user = User(
                    id=tg_user.id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    referred_by=referred_by,
                )
                session.add(user)
                return user

    async def get_user(self, user_id: int) -> User | None:
        async with self._session_factory() as session:
            return await session.get(User, user_id)

    async def get_user_by_username(self, username: str) -> User | None:
        clean = (username or "").strip().replace("@", "").lower()
        if not clean:
            return None
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(func.lower(User.username) == clean).limit(1)
            )
            return result.scalar_one_or_none()

    async def get_profile(self, user_id: int) -> ProfileSummary:
        async with self._session_factory() as session:
            user = await session.get(User, user_id)
            if not user:
                raise NotFoundError("Пользователь не найден")

            invited_q = await session.execute(
                select(func.count(User.id)).where(User.referred_by == user_id)
            )
            invited_count = invited_q.scalar_one()

            return ProfileSummary(
                user_id=user.id,
                balance=q_money(user.balance),
                total_bets=user.total_bets,
                total_wager=q_money(user.total_wager),
                invited_count=int(invited_count),
                referral_earnings=q_money(user.referral_earnings),
                username=user.username,
                first_name=user.first_name,
            )

    async def place_bet(
        self,
        *,
        user_id: int,
        game: str,
        stake: Decimal,
        details: dict[str, Any] | None = None,
    ) -> Bet:
        stake = q_money(stake)
        if stake <= 0:
            raise ValueError("Ставка должна быть больше нуля")

        async with self._session_factory() as session:
            async with session.begin():
                user = await session.get(User, user_id)
                if not user:
                    raise NotFoundError("Пользователь не найден")

                if q_money(user.balance) < stake:
                    raise BalanceError("Недостаточно средств")

                user.balance = q_money(user.balance - stake)
                user.total_bets += 1
                user.total_wager = q_money(user.total_wager + stake)

                bet = Bet(
                    user_id=user_id,
                    game=game,
                    stake=stake,
                    status="pending",
                    details=details or {},
                )
                session.add(bet)
                await session.flush()

                session.add(
                    Transaction(
                        user_id=user_id,
                        kind="bet",
                        amount=-stake,
                        status="completed",
                        description=f"Ставка на игру {game}",
                        details={"game": game, "bet_id": bet.id},
                    )
                )

                return bet

    async def finalize_bet(
        self,
        *,
        bet_id: int,
        status: str,
        payout: Decimal,
        base_multiplier: Decimal | None,
        applied_edge: Decimal | None,
        details: dict[str, Any] | None = None,
    ) -> Bet:
        payout = q_money(payout)
        merge_details = details or {}

        async with self._session_factory() as session:
            async with session.begin():
                bet = await session.get(Bet, bet_id)
                if not bet:
                    raise NotFoundError("Ставка не найдена")

                if bet.status != "pending":
                    return bet

                bet.status = status
                bet.payout = payout
                bet.base_multiplier = base_multiplier
                bet.applied_edge = applied_edge
                bet.details = {**(bet.details or {}), **merge_details}

                user = await session.get(User, bet.user_id)
                if not user:
                    raise NotFoundError("Пользователь ставки не найден")

                if payout > 0:
                    user.balance = q_money(user.balance + payout)
                    session.add(
                        Transaction(
                            user_id=user.id,
                            kind="win",
                            amount=payout,
                            status="completed",
                            description=f"Выплата по игре {bet.game}",
                            details={"game": bet.game, "bet_id": bet.id},
                        )
                    )

                if status == "lost" and user.referred_by and self._referral_rate > 0:
                    referrer = await session.get(User, user.referred_by)
                    if referrer:
                        referral_amount = q_money(bet.stake * self._referral_rate)
                        if referral_amount > 0:
                            referrer.balance = q_money(referrer.balance + referral_amount)
                            referrer.referral_earnings = q_money(
                                referrer.referral_earnings + referral_amount
                            )
                            session.add(
                                Transaction(
                                    user_id=referrer.id,
                                    kind="referral",
                                    amount=referral_amount,
                                    status="completed",
                                    description="Реферальная комиссия от проигрыша",
                                    details={
                                        "source_user_id": user.id,
                                        "bet_id": bet.id,
                                        "loss_amount": str(q_money(bet.stake)),
                                        "rate": float(self._referral_rate),
                                    },
                                )
                            )

                return bet

    async def grant_welcome_bonus_once(
        self,
        *,
        user_id: int,
        amount: Decimal,
    ) -> bool:
        amount = q_money(amount)
        if amount <= 0:
            raise ValueError("Сумма бонуса должна быть > 0")

        async with self._session_factory() as session:
            async with session.begin():
                user = await session.get(User, user_id)
                if not user:
                    raise NotFoundError("Пользователь не найден")

                exists = await session.execute(
                    select(Transaction.id)
                    .where(
                        Transaction.user_id == user_id,
                        Transaction.kind == "welcome_bonus",
                    )
                    .limit(1)
                )
                if exists.scalar_one_or_none() is not None:
                    return False

                user.balance = q_money(user.balance + amount)
                session.add(
                    Transaction(
                        user_id=user.id,
                        kind="welcome_bonus",
                        amount=amount,
                        status="completed",
                        description="Приветственный бонус",
                        details={},
                    )
                )
                return True

    async def create_invoice(
        self,
        *,
        user_id: int,
        invoice_id: int,
        amount: Decimal,
        asset: str,
        pay_url: str,
        payload: str | None = None,
    ) -> Invoice:
        amount = q_money(amount)

        async with self._session_factory() as session:
            async with session.begin():
                invoice = Invoice(
                    user_id=user_id,
                    invoice_id=invoice_id,
                    amount=amount,
                    asset=asset,
                    pay_url=pay_url,
                    payload=payload,
                    status="active",
                )
                session.add(invoice)
                return invoice

    async def get_active_invoices(self, limit: int = 50) -> list[Invoice]:
        async with self._session_factory() as session:
            query: Select[tuple[Invoice]] = (
                select(Invoice)
                .where(Invoice.status.in_(["active", "pending"]))
                .order_by(Invoice.created_at.asc())
                .limit(limit)
            )
            result = await session.execute(query)
            return list(result.scalars())

    async def get_user_invoices(
        self,
        *,
        user_id: int,
        statuses: list[str] | None = None,
        limit: int = 20,
    ) -> list[Invoice]:
        async with self._session_factory() as session:
            query: Select[tuple[Invoice]] = select(Invoice).where(Invoice.user_id == user_id)
            if statuses:
                query = query.where(Invoice.status.in_(statuses))

            query = query.order_by(Invoice.created_at.desc()).limit(limit)
            result = await session.execute(query)
            return list(result.scalars())

    async def mark_invoice_status(self, *, invoice_id: int, status: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                invoice = await session.get(Invoice, invoice_id)
                if not invoice:
                    raise NotFoundError("Инвойс не найден")
                invoice.status = status

    async def apply_paid_invoice(self, *, external_invoice_id: int, external_status: str) -> bool:
        status_raw = (external_status or "").strip().lower()
        paid_aliases = {"paid", "completed", "confirmed"}
        normalized_status = "paid" if status_raw in paid_aliases else status_raw

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(Invoice).where(Invoice.invoice_id == external_invoice_id)
                )
                invoice = result.scalar_one_or_none()
                if not invoice:
                    return False

                if normalized_status != "paid":
                    # Do not downgrade already paid invoices to non-paid statuses.
                    if invoice.status != "paid":
                        invoice.status = normalized_status
                    return False

                existing_tx = await session.execute(
                    select(Transaction.id)
                    .where(
                        Transaction.kind == "deposit",
                        Transaction.external_id == str(invoice.invoice_id),
                    )
                    .limit(1)
                )
                if existing_tx.scalar_one_or_none() is not None:
                    invoice.status = "paid"
                    return False

                user = await session.get(User, invoice.user_id)
                if not user:
                    return False

                invoice.status = "paid"
                user.balance = q_money(user.balance + invoice.amount)
                session.add(
                    Transaction(
                        user_id=user.id,
                        kind="deposit",
                        amount=invoice.amount,
                        status="completed",
                        external_id=str(invoice.invoice_id),
                        description="Пополнение через CryptoBot",
                        details={"asset": invoice.asset, "invoice_id": invoice.invoice_id},
                    )
                )
                return True

    async def transaction_exists(
        self,
        *,
        kind: str,
        external_id: str,
        user_id: int | None = None,
    ) -> bool:
        clean_external = (external_id or "").strip()
        if not clean_external:
            return False

        async with self._session_factory() as session:
            query = select(Transaction.id).where(
                Transaction.kind == kind,
                Transaction.external_id == clean_external,
            )
            if user_id is not None:
                query = query.where(Transaction.user_id == user_id)
            result = await session.execute(query.limit(1))
            return result.scalar_one_or_none() is not None

    async def process_withdrawal(
        self,
        *,
        user_id: int,
        amount: Decimal,
        asset: str,
        external_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        amount = q_money(amount)

        async with self._session_factory() as session:
            async with session.begin():
                user = await session.get(User, user_id)
                if not user:
                    raise NotFoundError("Пользователь не найден")
                if q_money(user.balance) < amount:
                    raise BalanceError("Недостаточно средств")

                user.balance = q_money(user.balance - amount)
                session.add(
                    Transaction(
                        user_id=user.id,
                        kind="withdrawal",
                        amount=-amount,
                        status="completed",
                        external_id=external_id,
                        description="Вывод через CryptoBot",
                        details={"asset": asset, **(details or {})},
                    )
                )

    async def credit_balance(
        self,
        *,
        user_id: int,
        amount: Decimal,
        kind: str,
        external_id: str | None = None,
        description: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        amount = q_money(amount)
        if amount <= 0:
            raise ValueError("Сумма зачисления должна быть > 0")

        async with self._session_factory() as session:
            async with session.begin():
                user = await session.get(User, user_id)
                if not user:
                    raise NotFoundError("Пользователь не найден")

                user.balance = q_money(user.balance + amount)
                session.add(
                    Transaction(
                        user_id=user.id,
                        kind=kind,
                        amount=amount,
                        status="completed",
                        external_id=external_id,
                        description=description,
                        details=details or {},
                    )
                )

    async def create_withdraw_request(
        self,
        *,
        user_id: int,
        amount: Decimal,
        details: dict[str, Any] | None = None,
    ) -> Transaction:
        amount = q_money(amount)
        if amount <= 0:
            raise ValueError("Сумма должна быть > 0")

        async with self._session_factory() as session:
            async with session.begin():
                user = await session.get(User, user_id)
                if not user:
                    raise NotFoundError("Пользователь не найден")
                if q_money(user.balance) < amount:
                    raise BalanceError("Недостаточно средств")

                user.balance = q_money(user.balance - amount)
                request_details = {**(details or {}), "balance_reserved": True}

                tx = Transaction(
                    user_id=user.id,
                    kind="withdraw_request",
                    amount=-amount,
                    status="pending",
                    description="Заявка на вывод",
                    details=request_details,
                )
                session.add(tx)
                await session.flush()
                return tx

    async def count_pending_withdraw_requests(self) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.kind == "withdraw_request",
                    Transaction.status == "pending",
                )
            )
            return int(result.scalar_one())

    async def list_pending_withdraw_requests(
        self,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[WithdrawRequestRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Transaction, User)
                .join(User, Transaction.user_id == User.id, isouter=True)
                .where(
                    Transaction.kind == "withdraw_request",
                    Transaction.status == "pending",
                )
                .order_by(Transaction.created_at.asc())
                .limit(limit)
                .offset(offset)
            )
            rows: list[WithdrawRequestRow] = []
            for tx, user in result.all():
                rows.append(
                    WithdrawRequestRow(
                        request_id=int(tx.id),
                        user_id=int(tx.user_id),
                        username=user.username if user else None,
                        first_name=user.first_name if user else None,
                        amount=q_money(abs(tx.amount)),
                        status=str(tx.status),
                        created_at=tx.created_at,
                        details=tx.details or {},
                    )
                )
            return rows

    async def approve_withdraw_request(
        self,
        *,
        request_id: int,
        admin_id: int,
    ) -> WithdrawApproveResult:
        async with self._session_factory() as session:
            async with session.begin():
                tx = await session.get(Transaction, request_id)
                if not tx:
                    raise NotFoundError("Заявка на вывод не найдена")
                if tx.kind != "withdraw_request":
                    raise NotFoundError("Транзакция не является заявкой на вывод")
                if tx.status != "pending":
                    raise BalanceError("Заявка уже обработана")

                user = await session.get(User, tx.user_id)
                if not user:
                    raise NotFoundError("Пользователь заявки не найден")

                amount = q_money(abs(tx.amount))
                details = tx.details or {}
                if not bool(details.get("balance_reserved")):
                    # Backward compatibility for old requests created before balance reservation.
                    if q_money(user.balance) < amount:
                        raise BalanceError("Недостаточно средств для подтверждения старой заявки")
                    user.balance = q_money(user.balance - amount)

                tx.status = "completed"
                tx.description = "Заявка на вывод подтверждена администратором"
                tx.details = {**details, "approved_by_admin_id": admin_id, "balance_reserved": True}

                return WithdrawApproveResult(
                    request_id=int(tx.id),
                    user_id=int(tx.user_id),
                    amount=amount,
                )

    async def create_game_session(
        self,
        *,
        user_id: int,
        bet_id: int,
        game: str,
        state: dict[str, Any],
    ) -> GameSession:
        async with self._session_factory() as session:
            async with session.begin():
                session_id = str(uuid.uuid4())
                game_session = GameSession(
                    id=session_id,
                    user_id=user_id,
                    bet_id=bet_id,
                    game=game,
                    status="active",
                    state=state,
                )
                session.add(game_session)
                return game_session

    async def get_active_game_session(self, *, user_id: int, game: str) -> GameSession | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(GameSession)
                .where(
                    GameSession.user_id == user_id,
                    GameSession.game == game,
                    GameSession.status == "active",
                )
                .order_by(GameSession.created_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def update_game_session(
        self,
        *,
        session_id: str,
        state: dict[str, Any],
        status: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                game_session = await session.get(GameSession, session_id)
                if not game_session:
                    raise NotFoundError("Игровая сессия не найдена")
                game_session.state = state
                if status:
                    game_session.status = status

    async def get_game_session(self, session_id: str) -> GameSession | None:
        async with self._session_factory() as session:
            return await session.get(GameSession, session_id)

    async def get_ref_link(self, user_id: int, bot_username: str) -> str:
        return f"https://t.me/{bot_username}?start=ref_{user_id}"

    async def get_system_stats(self) -> SystemStats:
        async with self._session_factory() as session:
            users_count_q = await session.execute(select(func.count(User.id)))
            total_balance_q = await session.execute(select(func.coalesce(func.sum(User.balance), 0)))
            total_bets_q = await session.execute(select(func.coalesce(func.sum(User.total_bets), 0)))
            total_wager_q = await session.execute(select(func.coalesce(func.sum(User.total_wager), 0)))
            return SystemStats(
                users_count=int(users_count_q.scalar_one()),
                total_balance=q_money(total_balance_q.scalar_one()),
                total_bets=int(total_bets_q.scalar_one()),
                total_wager=q_money(total_wager_q.scalar_one()),
            )

    async def count_users(self) -> int:
        async with self._session_factory() as session:
            result = await session.execute(select(func.count(User.id)))
            return int(result.scalar_one())

    async def list_users_balances(self, *, limit: int = 20, offset: int = 0) -> list[UserBalanceRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(User)
                .order_by(desc(User.balance), User.id.asc())
                .limit(limit)
                .offset(offset)
            )
            rows: list[UserBalanceRow] = []
            for user in result.scalars().all():
                rows.append(
                    UserBalanceRow(
                        user_id=user.id,
                        username=user.username,
                        first_name=user.first_name,
                        balance=q_money(user.balance),
                        total_bets=user.total_bets,
                        total_wager=q_money(user.total_wager),
                    )
                )
            return rows

    async def get_all_user_ids(self) -> list[int]:
        async with self._session_factory() as session:
            result = await session.execute(select(User.id).order_by(User.id.asc()))
            return [int(v) for v in result.scalars().all()]

    async def get_app_setting(self, key: str) -> str | None:
        async with self._session_factory() as session:
            setting = await session.get(AppSetting, key)
            if not setting:
                return None
            return setting.value

    async def set_app_setting(self, *, key: str, value: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                setting = await session.get(AppSetting, key)
                if setting:
                    setting.value = value
                else:
                    session.add(AppSetting(key=key, value=value))

    def _parse_referral(self, payload: str | None) -> int | None:
        if not payload:
            return None
        if not payload.startswith("ref_"):
            return None
        value = payload.replace("ref_", "", 1).strip()
        if not value.isdigit():
            return None
        return int(value)

