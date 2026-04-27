from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str
    postgres_user: str = "sqf"
    postgres_password: str = "change_me"
    postgres_db: str = "sports_quant_fund"

    # Redis — NON usare redis_url direttamente: manca autenticazione.
    # Usare SEMPRE settings.redis_url_with_auth
    redis_password: str = ""

    @property
    def redis_url_with_auth(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@redis:6379/0"
        return "redis://redis:6379/0"

    # Auth
    secret_key: str
    access_token_expire_minutes: int = 1440
    admin_email: str = "giuseppe@localhost"
    admin_password: str = "change_me"

    # Anthropic
    anthropic_api_key: str
    claude_model: str = "claude-haiku-4-5-20251001"       # agenti analisi (fast + cheap)
    claude_model_premium: str = "claude-sonnet-4-6"        # riservato per uso futuro

    # OddsPapi (oddspapi.io) — 250 req/mese, include Bet365 ed Eplay24
    oddspapi_key: str = ""

    # The Odds API — fino a 3 chiavi in rotazione automatica (500 req/mese ciascuna)
    odds_api_key: str
    odds_api_key_2: str = ""
    odds_api_key_3: str = ""
    odds_api_key_4: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    odds_staleness_seconds: int = 3600  # 1 hour — aligned with 6h quote freshness filter

    # API-Football (api-sports.io)
    api_football_key: str = ""
    api_football_host: str = "api-football-v1.p.rapidapi.com"

    # football-data.org
    football_data_key: str = ""

    # Tennis API
    tennis_api_key: str = ""
    tennis_api_host: str = "tennis-live-data.p.rapidapi.com"

    # News API
    news_api_key: str = ""

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_webhook_url: str = ""  # es. https://178.104.205.229/api/telegram/webhook

    # Bankroll management
    initial_bankroll: float = Field(default=1000.0, gt=0)
    kelly_multiplier: float = Field(default=0.25, gt=0.0, le=1.0)
    max_daily_exposure_pct: float = Field(default=0.12, gt=0.0, le=0.5)
    scalata_start_amount: float = Field(default=20.0, gt=0)  # importo di partenza scalate auto

    # System
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:3000"


settings = Settings()
