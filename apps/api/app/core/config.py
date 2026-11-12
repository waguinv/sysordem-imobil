"""Configuração lida do ambiente. Segredos nunca têm valor padrão utilizável."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    # Conexão da aplicação: papel SEM superuser e SEM BYPASSRLS (sujeito ao RLS).
    database_url: str = "postgresql+psycopg://imobil_app:local_only@localhost:5432/imobil"
    # Conexão das migrations: papel dono das tabelas.
    migration_database_url: str | None = None

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    jwt_issuer: str = "sysordem-imobil"

    cors_origins: list[str] = ["http://localhost:3000"]

    # Validade padrão da reserva de lote (hipótese: 72 h; configurável por empresa em fase futura)
    reservation_default_hours: int = 72
    reservation_max_hours: int = 720

    login_max_failures: int = 5
    login_window_seconds: int = 900

    @field_validator("jwt_secret")
    @classmethod
    def _no_placeholder(cls, v: str) -> str:
        if v.upper().startswith("CHANGE_ME"):
            raise ValueError("JWT_SECRET ainda está com o valor de exemplo")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
