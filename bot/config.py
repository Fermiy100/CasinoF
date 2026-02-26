from __future__ import annotations

from functools import lru_cache
from typing import Set

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(validation_alias="BOT_TOKEN")
    bot_username: str = Field(default="casino_bot", validation_alias="BOT_USERNAME")
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./casino.db",
        validation_alias="DATABASE_URL",
    )

    cryptobot_api_token: str = Field(default="", validation_alias="CRYPTOBOT_API_TOKEN")
    cryptobot_api_base: str = Field(
        default="https://pay.crypt.bot/api",
        validation_alias="CRYPTOBOT_API_BASE",
    )
    cryptobot_default_asset: str = Field(default="USDT", validation_alias="CRYPTOBOT_DEFAULT_ASSET")
    cryptobot_assets_raw: str = Field(default="USDT,TON", validation_alias="CRYPTOBOT_ASSETS")
    cryptobot_fiat: str = Field(default="USD", validation_alias="CRYPTOBOT_FIAT")
    enable_telegram_stars: bool = Field(default=True, validation_alias="ENABLE_TELEGRAM_STARS")
    stars_usd_rate: float = Field(default=0.017, validation_alias="STARS_USD_RATE")
    support_username: str = Field(default="fermiy100", validation_alias="SUPPORT_USERNAME")
    bets_channel: str = Field(default="@fermiyyy", validation_alias="BETS_CHANNEL")
    menu_banner: str = Field(default="https://t.me/fermiyyy/38", validation_alias="MENU_BANNER")
    win_banner: str = Field(default="https://t.me/fermiyyy/40", validation_alias="WIN_BANNER")
    loss_banner: str = Field(default="https://t.me/fermiyyy/39", validation_alias="LOSS_BANNER")

    house_edge_slots: float = Field(default=0.18, validation_alias="HOUSE_EDGE_SLOTS")
    house_edge_dice: float = Field(default=0.35, validation_alias="HOUSE_EDGE_DICE")
    house_edge_crash: float = Field(default=0.22, validation_alias="HOUSE_EDGE_CRASH")
    house_edge_roulette: float = Field(default=0.25, validation_alias="HOUSE_EDGE_ROULETTE")
    house_edge_mines: float = Field(default=0.18, validation_alias="HOUSE_EDGE_MINES")

    referral_rate: float = Field(default=0.10, validation_alias="REFERRAL_RATE")
    min_bet: float = Field(default=0.1, validation_alias="MIN_BET")
    max_bet: float = Field(default=10_000, validation_alias="MAX_BET")

    invoice_poll_interval_sec: int = Field(default=12, validation_alias="INVOICE_POLL_INTERVAL_SEC")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    @property
    def admin_ids(self) -> Set[int]:
        result: Set[int] = set()
        for part in self.admin_ids_raw.split(","):
            clean = part.strip()
            if clean.isdigit():
                result.add(int(clean))
        return result

    @property
    def cryptobot_assets(self) -> list[str]:
        assets: list[str] = []
        for part in self.cryptobot_assets_raw.split(","):
            clean = part.strip().upper()
            if clean and clean.isalnum() and clean not in assets:
                assets.append(clean)
        if self.cryptobot_default_asset.upper() not in assets:
            assets.insert(0, self.cryptobot_default_asset.upper())
        return assets


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

