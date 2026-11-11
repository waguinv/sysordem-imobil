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
    # Validade padrão/máxima da proposta em dias (hipótese: 7 / 90)
    proposal_default_days: int = 7
    proposal_max_days: int = 90

    # Fuso das datas de negócio (vencimentos, validade, atraso)
    business_timezone: str = "America/Sao_Paulo"

    # Pagamentos (Fase 4a). O PSP "sandbox" simula cobranças Pix/boleto e assina seus webhooks com
    # HMAC-SHA256. Fica DESLIGADO em produção (a menos que habilitado explicitamente). Sem segredo
    # configurado, um segredo aleatório é gerado por processo (bom para dev; webhooks externos exigem
    # PAYMENTS_SANDBOX_SECRET fixo).
    payments_sandbox_enabled: bool | None = None
    payments_sandbox_secret: str | None = Field(default=None, min_length=32)
    # Janela aceita para o carimbo de tempo do webhook (proteção contra replay)
    payments_webhook_tolerance_seconds: int = 300

    login_max_failures: int = 5
    login_window_seconds: int = 900

    @field_validator("jwt_secret")
    @classmethod
    def _no_placeholder(cls, v: str) -> str:
        if v.upper().startswith("CHANGE_ME"):
            raise ValueError("JWT_SECRET ainda está com o valor de exemplo")
        return v

    @field_validator("payments_sandbox_secret", mode="before")
    @classmethod
    def _no_placeholder_sandbox(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None  # vazio (ex.: docker compose sem a variável) = não configurado
        if str(v).upper().startswith("CHANGE_ME"):
            raise ValueError("PAYMENTS_SANDBOX_SECRET ainda está com o valor de exemplo")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sandbox_enabled(self) -> bool:
        if self.payments_sandbox_enabled is None:
            return not self.is_production
        return self.payments_sandbox_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
