import sys

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.secrets import decrypt_secret_if_needed


_RUNNING_PYTEST = "pytest" in sys.modules
_DEFAULT_DB_URL = "sqlite:///./posterpro_test.db" if _RUNNING_PYTEST else "sqlite:///./posterpro.db"
_ENV_FILE = None if _RUNNING_PYTEST else ".env"


class Settings(BaseSettings):
    app_name: str = "PosterPro"
    app_base_url: str | None = None
    environment: str = "development"
    cors_allowed_origins: str | None = None
    # Keep local defaults credential-free; production should supply explicit env vars.
    database_url: str = _DEFAULT_DB_URL
    redis_url: str = "redis://localhost:6379/1"
    storage_root: str = "./storage"
    startup_schema_compat_enabled: bool = True
    session_secret: str | None = None
    openai_api_key_plain: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY"))
    openai_api_key_enc: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY_ENC"))
    ebay_client_id: str | None = None
    ebay_client_secret_plain: str | None = Field(default=None, validation_alias=AliasChoices("EBAY_CLIENT_SECRET"))
    ebay_client_secret_enc: str | None = Field(default=None, validation_alias=AliasChoices("EBAY_CLIENT_SECRET_ENC"))
    ebay_runame: str | None = None
    ebay_redirect_uri: str | None = None
    photoroom_api_key_plain: str | None = Field(default=None, validation_alias=AliasChoices("PHOTOROOM_API_KEY"))
    photoroom_api_key_enc: str | None = Field(default=None, validation_alias=AliasChoices("PHOTOROOM_API_KEY_ENC"))
    photoroom_api_url: str = "https://sdk.photoroom.com/v1/segment"
    automation_bridge_enabled: bool = False
    automation_bridge_url: str | None = None
    automation_bridge_timeout_seconds: int = 30
    automation_bridge_vnc_host: str = "127.0.0.1"
    automation_bridge_vnc_port: int = 5901
    automation_bridge_api_key_plain: str | None = Field(default=None, validation_alias=AliasChoices("AUTOMATION_BRIDGE_API_KEY"))
    automation_bridge_api_key_enc: str | None = Field(default=None, validation_alias=AliasChoices("AUTOMATION_BRIDGE_API_KEY_ENC"))
    autonomous_mode: bool = True
    autonomous_dry_run: bool = False
    autonomous_crosspost_enabled: bool = True
    auto_relist_enabled: bool = True
    auto_relist_min_price: float = 20.0
    auto_relist_user_rules_json: str | None = None
    sale_detection_enabled: bool = True
    sale_detection_dry_run: bool = True
    sale_detection_poll_minutes: int = 15
    max_concurrent_bulk_tasks: int = 50
    bulk_chunk_size: int = 0
    amazon_vine_import_enabled: bool = False
    amazon_vine_import_premium_only: bool = False
    amazon_media_lookup_enabled: bool = False
    amazon_media_page_fallback_enabled: bool = False
    amazon_marketplace_region: str = "US"
    amazon_media_fetch_mode: str = "manual_only"
    amazon_media_rate_limit_per_minute: int = 12
    amazon_paapi_access_key_enc: str | None = None
    amazon_paapi_secret_key_enc: str | None = None
    amazon_paapi_partner_tag_enc: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password_plain: str | None = Field(default=None, validation_alias=AliasChoices("SMTP_PASSWORD"))
    smtp_password_enc: str | None = Field(default=None, validation_alias=AliasChoices("SMTP_PASSWORD_ENC"))
    smtp_from_email: str | None = None
    smtp_from_name: str | None = None
    smtp_use_tls: bool = True

    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    @property
    def openai_api_key(self) -> str | None:
        return decrypt_secret_if_needed(self.openai_api_key_enc, secret_key=self.session_secret) or self.openai_api_key_plain

    @property
    def ebay_client_secret(self) -> str | None:
        return decrypt_secret_if_needed(self.ebay_client_secret_enc, secret_key=self.session_secret) or self.ebay_client_secret_plain

    @property
    def photoroom_api_key(self) -> str | None:
        return decrypt_secret_if_needed(self.photoroom_api_key_enc, secret_key=self.session_secret) or self.photoroom_api_key_plain

    @property
    def automation_bridge_api_key(self) -> str | None:
        return decrypt_secret_if_needed(self.automation_bridge_api_key_enc, secret_key=self.session_secret) or self.automation_bridge_api_key_plain

    @property
    def amazon_paapi_access_key(self) -> str | None:
        return decrypt_secret_if_needed(self.amazon_paapi_access_key_enc, secret_key=self.session_secret)

    @property
    def amazon_paapi_secret_key(self) -> str | None:
        return decrypt_secret_if_needed(self.amazon_paapi_secret_key_enc, secret_key=self.session_secret)

    @property
    def amazon_paapi_partner_tag(self) -> str | None:
        return decrypt_secret_if_needed(self.amazon_paapi_partner_tag_enc, secret_key=self.session_secret)

    @property
    def smtp_password(self) -> str | None:
        return decrypt_secret_if_needed(self.smtp_password_enc, secret_key=self.session_secret) or self.smtp_password_plain


settings = Settings()
