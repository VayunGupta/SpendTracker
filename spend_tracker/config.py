from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_secret_key: str = Field(default="dev-only-change-me")
    database_url: str = Field(default="sqlite:///./data/spend_tracker.db")

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"
    plaid_products: str = "transactions"
    plaid_country_codes: str = "US"
    plaid_redirect_uri: Optional[str] = None

    splitwise_api_key: str = ""
    splitwise_user_id: Optional[int] = None

    @field_validator("plaid_redirect_uri", "splitwise_user_id", mode="before")
    @classmethod
    def blank_optional_values_are_none(cls, value):
        if value == "":
            return None
        return value

    @property
    def plaid_base_url(self) -> str:
        urls = {
            "sandbox": "https://sandbox.plaid.com",
            "development": "https://production.plaid.com",
            "production": "https://production.plaid.com",
        }
        return urls.get(self.plaid_env, urls["sandbox"])

    @property
    def plaid_products_list(self) -> List[str]:
        return [item.strip() for item in self.plaid_products.split(",") if item.strip()]

    @property
    def plaid_country_codes_list(self) -> List[str]:
        return [item.strip() for item in self.plaid_country_codes.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
