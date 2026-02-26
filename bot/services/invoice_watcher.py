from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from bot.db.repository import CasinoRepository
from bot.services.cryptobot import CryptoBotClient

logger = logging.getLogger(__name__)


async def run_invoice_polling(
    repo: CasinoRepository,
    crypto: CryptoBotClient,
    interval_sec: int,
) -> None:
    while True:
        try:
            await _poll_once(repo, crypto)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка проверки инвойсов")

        await asyncio.sleep(max(5, interval_sec))


async def _poll_once(repo: CasinoRepository, crypto: CryptoBotClient) -> None:
    if not crypto.enabled:
        return

    active_invoices = await repo.get_active_invoices(limit=100)
    if not active_invoices:
        return

    invoice_ids = [i.invoice_id for i in active_invoices]

    for chunk in _chunks(invoice_ids, 50):
        remote = await crypto.get_invoices(invoice_ids=list(chunk))
        for remote_invoice in remote:
            invoice_id = int(remote_invoice.get("invoice_id", 0))
            status = str(remote_invoice.get("status", "")).lower().strip()
            if not invoice_id or not status:
                continue

            if status in {"paid", "completed", "confirmed", "expired", "cancelled", "invalid"}:
                changed = await repo.apply_paid_invoice(
                    external_invoice_id=invoice_id,
                    external_status=status,
                )
                if changed and status in {"paid", "completed", "confirmed"}:
                    logger.info("Invoice %s credited", invoice_id)


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]

