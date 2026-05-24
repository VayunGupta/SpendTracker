from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from spend_tracker.config import Settings


class SplitwiseClient:
    base_url = "https://secure.splitwise.com/api/v3.0"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.settings.splitwise_api_key}"}
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=30) as client:
            try:
                response = await client.get(path, params=params)
            except httpx.RequestError as exc:
                raise SplitwiseApiError(502, f"Could not reach Splitwise: {exc}") from exc
            if response.is_error:
                raise SplitwiseApiError(response.status_code, splitwise_error_detail(response))
            return response.json()

    async def get_current_user(self) -> Dict[str, Any]:
        return (await self._get("/get_current_user")).get("user", {})

    async def get_expenses(
        self,
        limit: int = 100,
        offset: int = 0,
        updated_after: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if updated_after:
            params["updated_after"] = updated_after
        return (await self._get("/get_expenses", params)).get("expenses", [])


class SplitwiseApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def splitwise_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"Splitwise request failed with HTTP {response.status_code}"
    error = body.get("error") or body.get("errors")
    if error:
        if isinstance(error, str) and "not logged in" in error.lower():
            return (
                "Splitwise rejected the token: you are not logged in. "
                "Use the API key from your Splitwise app's project detail page, "
                "or an OAuth access token, not the consumer key or consumer secret."
            )
        return f"Splitwise request failed: {error}"
    return f"Splitwise request failed with HTTP {response.status_code}"
