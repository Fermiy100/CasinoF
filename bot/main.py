from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import get_settings
from bot.db.repository import CasinoRepository
from bot.db.session import create_engine_and_sessionmaker
from bot.handlers import router
from bot.services.cryptobot import CryptoBotClient
from bot.services.invoice_watcher import run_invoice_polling


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    lock_path = _acquire_local_lock()
    try:
        settings = get_settings()
        engine, session_factory = create_engine_and_sessionmaker(settings)
        repo = CasinoRepository(session_factory, settings)
        await repo.init_db(engine)

        bot_token = settings.bot_token.strip().strip('"').strip("'")
        if not bot_token:
            raise RuntimeError(
                "BOT_TOKEN is empty in .env. Set BOT_TOKEN from @BotFather and restart."
            )

        bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = Dispatcher()
        dp.include_router(router)

        crypto = CryptoBotClient(
            api_token=settings.cryptobot_api_token,
            base_url=settings.cryptobot_api_base,
        )

        await _set_commands(bot)

        watcher_task = asyncio.create_task(
            run_invoice_polling(repo, crypto, settings.invoice_poll_interval_sec),
            name="invoice-poller",
        )

        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(
                bot,
                repo=repo,
                settings=settings,
                crypto=crypto,
            )
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

            await crypto.close()
            await bot.session.close()
            await engine.dispose()
    finally:
        _release_local_lock(lock_path)


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск бота"),
            BotCommand(command="games", description="Выбрать игру"),
            BotCommand(command="bet", description="Сделать ставку"),
            BotCommand(command="balance", description="Показать баланс"),
            BotCommand(command="profile", description="Профиль игрока"),
            BotCommand(command="deposit", description="Пополнить баланс"),
            BotCommand(command="withdraw", description="Вывести средства"),
            BotCommand(command="ref", description="Реферальная программа"),
            BotCommand(command="support", description="Техподдержка"),
            BotCommand(command="admin", description="Админ-панель"),
        ]
    )


def _acquire_local_lock() -> Path:
    lock_path = Path(".bot.polling.lock")
    current_pid = os.getpid()

    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            existing_pid = None

        if existing_pid and _pid_exists(existing_pid):
            raise RuntimeError(
                f"Локальный инстанс уже запущен (PID {existing_pid}). "
                "Остановите его перед новым запуском."
            )
        lock_path.unlink(missing_ok=True)

    lock_path.write_text(str(current_pid), encoding="utf-8")
    return lock_path


def _release_local_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Graceful stop without noisy traceback when user presses Ctrl+C.
        print("Bot stopped by user.")
