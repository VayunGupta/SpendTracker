from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from spend_tracker.config import Settings


class PlaidApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class PlaidClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        auth_payload = {
            "client_id": self.settings.plaid_client_id,
            "secret": self.settings.plaid_secret,
            **payload,
        }
        async with httpx.AsyncClient(base_url=self.settings.plaid_base_url, timeout=30) as client:
            try:
                response = await client.post(path, json=auth_payload)
            except httpx.RequestError as exc:
                raise PlaidApiError(
                    502,
                    f"Could not reach Plaid at {self.settings.plaid_base_url}: {exc}",
                ) from exc
            if response.is_error:
                raise PlaidApiError(response.status_code, plaid_error_detail(response))
            return response.json()

    async def create_link_token(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "client_name": "Spend Tracker",
            "language": "en",
            "country_codes": self.settings.plaid_country_codes_list,
            "user": {"client_user_id": "self-hosted-user"},
            "products": self.settings.plaid_products_list,
        }
        if self.settings.plaid_redirect_uri:
            payload["redirect_uri"] = self.settings.plaid_redirect_uri
        return await self._post("/link/token/create", payload)

    async def exchange_public_token(self, public_token: str) -> Dict[str, Any]:
        return await self._post("/item/public_token/exchange", {"public_token": public_token})

    async def get_item(self, access_token: str) -> Dict[str, Any]:
        return await self._post("/item/get", {"access_token": access_token})

    async def get_accounts(self, access_token: str) -> Dict[str, Any]:
        return await self._post("/accounts/get", {"access_token": access_token})

    async def sync_transactions(self, access_token: str, cursor: Optional[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"access_token": access_token}
        if cursor:
            payload["cursor"] = cursor
        return await self._post("/transactions/sync", payload)


def plaid_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"Plaid request failed with HTTP {response.status_code}"

    error_code = body.get("error_code")
    error_message = body.get("error_message")
    display_message = body.get("display_message")
    parts = [part for part in [error_code, error_message, display_message] if part]
    if parts:
        return " - ".join(parts)
    return f"Plaid request failed with HTTP {response.status_code}"
