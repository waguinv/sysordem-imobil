"""Configuração lida do ambiente. Segredos nunca têm valor padrão utilizável."""

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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
    # Fase 5c: sandbox do PSP da PLATAFORMA (cobra a assinatura SaaS). Separado do sandbox das empresas e
    # PROIBIDO em produção — senão o simulador deixaria um proprietário "pagar" a própria assinatura.
    billing_sandbox_enabled: bool | None = None
    # Janela aceita para o carimbo de tempo do webhook (proteção contra replay)
    payments_webhook_tolerance_seconds: int = 300

    # Assinatura eletrônica. Provedor real: Documenso auto-hospedado (API v2). O simulador ("sandbox")
    # fica fora de produção por padrão, como o de pagamentos.
    documenso_url: str | None = None  # ex.: https://assinar.suaempresa.com.br (sem /api/v2)
    documenso_api_token: str | None = None
    documenso_webhook_secret: str | None = Field(default=None, min_length=32)
    esign_sandbox_enabled: bool | None = None
    esign_http_timeout_seconds: float = 20.0

    # Site público (Fase 5a): limite do formulário de contato (por empresa e por contato)
    public_leads_per_tenant_per_hour: int = 60
    public_leads_per_contact_per_hour: int = 3

    # Portal do comprador: sessão e validade do convite
    portal_token_minutes: int = 120
    portal_invite_days: int = 7

    login_max_failures: int = 5
    login_window_seconds: int = 900

    # Fase 6a: camada HTTP
    max_body_bytes: int = 5 * 1024 * 1024  # extrato OFX/CSV de até 2 MB em JSON cabe com folga
    webhook_rate_per_minute: int = 600  # por IP de origem, nas rotas /api/v1/webhooks/*
    log_format: str | None = None  # "json" | "text"; padrão: json em produção/staging
    log_level: str = "INFO"
    db_connect_timeout_seconds: int = 5
    db_pool_timeout_seconds: float = 10.0
    # Por processo. Deve cobrir as threads que atendem requisições ao mesmo tempo (Fase 6b: 5+10 limitava
    # a carga). Some todos os processos/réplicas e confira com max_connections do PostgreSQL.
    db_pool_size: int = 20
    db_max_overflow: int = 10

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

    @field_validator("documenso_url", "documenso_api_token", "documenso_webhook_secret", mode="before")
    @classmethod
    def _blank_documenso(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None  # vazio = assinatura eletrônica real não configurada
        if str(v).upper().startswith("CHANGE_ME"):
            raise ValueError("Configuração do Documenso ainda está com o valor de exemplo")
        return str(v).strip()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _no_billing_sandbox_in_production(self) -> "Settings":
        if self.is_production and self.billing_sandbox_enabled:
            raise ValueError("BILLING_SANDBOX_ENABLED não pode ser ligado em produção")
        return self

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        """Fase 6a: em produção, recusa subir com configuração de desenvolvimento."""
        if not self.is_production:
            return self
        problems = []
        if "local_only" in self.database_url or "CHANGE_ME" in self.database_url:
            problems.append("DATABASE_URL usa a senha de exemplo")
        if self.migration_database_url and self.migration_database_url == self.database_url:
            problems.append("a API não pode usar o papel das migrations")
        bad_origins = [o for o in self.cors_origins if not o.startswith("https://")]
        if bad_origins:
            problems.append(f"CORS_ORIGINS precisa ser https em produção: {bad_origins}")
        if self.payments_sandbox_enabled:
            problems.append("PAYMENTS_SANDBOX_ENABLED não pode ser ligado em produção")
        if self.esign_sandbox_enabled:
            problems.append("ESIGN_SANDBOX_ENABLED não pode ser ligado em produção")
        if len(self.jwt_secret) < 48:
            problems.append("JWT_SECRET deve ter 48+ caracteres em produção")
        if problems:
            raise ValueError("Configuração inválida para produção: " + "; ".join(problems))
        return self

    @property
    def effective_log_format(self) -> str:
        if self.log_format in ("json", "text"):
            return self.log_format
        return "json" if self.environment in ("production", "staging") else "text"

    @property
    def billing_sandbox(self) -> bool:
        if self.is_production:
            return False
        return True if self.billing_sandbox_enabled is None else self.billing_sandbox_enabled

    @property
    def esign_sandbox(self) -> bool:
        if self.is_production:
            return False
        return True if self.esign_sandbox_enabled is None else self.esign_sandbox_enabled

    @property
    def documenso_configured(self) -> bool:
        return bool(self.documenso_url and self.documenso_api_token and self.documenso_webhook_secret)

    @property
    def sandbox_enabled(self) -> bool:
        if self.payments_sandbox_enabled is None:
            return not self.is_production
        return self.payments_sandbox_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
