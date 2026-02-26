from __future__ import annotations

from typing import Any

import httpx


class CryptoBotError(Exception):
    pass


class CryptoBotClient:
    def __init__(self, api_token: str, base_url: str) -> None:
        self._api_token = api_token.strip()
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=15,
            headers={
                "Crypto-Pay-API-Token": self._api_token,
                "Content-Type": "application/json",
            },
        )

    @property
    def enabled(self) -> bool:
        return bool(self._api_token)

    async def close(self) -> None:
        await self._client.aclose()

    async def create_invoice(
        self,
        *,
        amount: str,
        asset: str,
        description: str,
        payload: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise CryptoBotError("CRYPTOBOT_API_TOKEN не задан")

        body: dict[str, Any] = {
            "amount": amount,
            "asset": asset,
            "description": description,
        }
        if payload:
            body["payload"] = payload

        return await self._request("/createInvoice", body)

    async def get_invoices(self, *, invoice_ids: list[int] | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        body: dict[str, Any] = {}
        if invoice_ids:
            body["invoice_ids"] = ",".join(str(i) for i in invoice_ids)

        result = await self._request("/getInvoices", body)
        items = result.get("items", [])
        if isinstance(items, list):
            return items
        return []

    async def transfer(
        self,
        *,
        user_id: int,
        asset: str,
        amount: str,
        spend_id: str,
        comment: str,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise CryptoBotError("CRYPTOBOT_API_TOKEN не задан")

        body = {
            "user_id": user_id,
            "asset": asset,
            "amount": amount,
            "spend_id": spend_id,
            "comment": comment,
        }
        return await self._request("/transfer", body)

    async def _request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(path, json=body)
        if response.status_code >= 400:
            raise CryptoBotError(
                f"CryptoBot HTTP {response.status_code}: {response.text[:250]}"
            )

        payload = response.json()
        if not payload.get("ok"):
            error = payload.get("error", {})
            message = error.get("name") if isinstance(error, dict) else str(error)
            raise CryptoBotError(f"CryptoBot API error: {message or 'unknown'}")

        result = payload.get("result")
        if not isinstance(result, dict):
            raise CryptoBotError("Некорректный ответ CryptoBot")
        return result

