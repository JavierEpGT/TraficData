"""Application settings from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets must never be logged."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    mapbox_access_token: str = Field(..., alias="MAPBOX_ACCESS_TOKEN")
    student_id: str = Field(..., alias="STUDENT_ID")
    secret_key: str = Field(..., alias="SECRET_KEY")
    dw_endpoint_url: str | None = Field(default=None, alias="DW_ENDPOINT_URL")
    dw_auth_token: str | None = Field(default=None, alias="DW_AUTH_TOKEN")
    dry_run: bool = Field(default=True, alias="DRY_RUN")
    segment_length_m: float = Field(default=400.0, alias="SEGMENT_LENGTH_M")
    segment_bearing_deg: float = Field(default=0.0, alias="SEGMENT_BEARING_DEG")
    db_path: str = Field(default="traffic.db", alias="DB_PATH")
    group1_api_url: str | None = Field(default=None, alias="GROUP1_API_URL")
    group1_api_token: str | None = Field(default=None, alias="GROUP1_API_TOKEN")
    g4_feed_token: str | None = Field(default=None, alias="G4_FEED_TOKEN")

    @property
    def should_send_to_dw(self) -> bool:
        """Send only when not dry-run and a DW endpoint is configured."""
        return (not self.dry_run) and bool(self.dw_endpoint_url)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()  # type: ignore[call-arg]
