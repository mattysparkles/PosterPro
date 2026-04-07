from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PosterPro"
    environment: str = "development"
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/posterpro"
    redis_url: str = "redis://localhost:6379/0"
    storage_root: str = "./storage"
    openai_api_key: str | None = None
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_redirect_uri: str | None = None
    photoroom_api_key: str | None = None
    photoroom_api_url: str = "https://sdk.photoroom.com/v1/segment"
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
