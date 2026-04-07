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
    autonomous_mode: bool = True
    autonomous_dry_run: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
