"""Centralized configuration management for the Rahasya OSINT Platform.

Uses pydantic-settings to load environment variables and .env file.
All sub-settings are composable and independently configurable.
"""

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    """PostgreSQL connection and pooling configuration."""
    url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/rahasya",
        description="Async PostgreSQL connection URL",
    )
    pool_size: int = Field(default=20, description="Base connection pool size")
    max_overflow: int = Field(default=10, description="Extra connections beyond pool_size")
    echo: bool = Field(default=False, description="Log all SQL queries")


class RedisSettings(BaseModel):
    """Redis cache and message broker configuration."""
    url: str = Field(default="redis://localhost:6379/0")
    db_number: int = Field(default=0)
    pubsub_enabled: bool = Field(default=False, description="Publish scan events across processes")


class CelerySettings(BaseModel):
    """Celery task queue configuration."""
    broker_url: str = Field(default="redis://localhost:6379/1")
    result_backend: str = Field(default="redis://localhost:6379/2")
    worker_concurrency: int = Field(default=4)
    task_serializer: str = Field(default="json")
    result_serializer: str = Field(default="json")
    enabled: bool = Field(default=False, description="Dispatch scans to Celery workers")


class TorSettings(BaseModel):
    """Tor proxy and circuit management configuration."""
    enabled: bool = Field(default=False, description="Enable Tor routing")
    socks_port: int = Field(default=9050)
    control_port: int = Field(default=9051)
    password: Optional[str] = Field(default=None, description="Tor control password")


class ScanSettings(BaseModel):
    """OSINT scan execution limits."""
    max_depth: int = Field(default=3, description="Maximum BFS recursion depth")
    max_entities: int = Field(default=500, description="Maximum entities per scan")
    max_time_minutes: int = Field(default=30, description="Scan timeout in minutes")
    confidence_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="Minimum confidence to include entity",
    )
    module_timeout_seconds: float = Field(
        default=30.0, gt=0, description="Hard timeout for each discovery module",
    )
    poll_interval_seconds: int = Field(
        default=2, ge=1, description="Dashboard polling interval for active scans",
    )


class StorageSettings(BaseModel):
    """Local durable storage used when a production database is unavailable."""

    scan_dir: Path = Field(default=Path("data/scans"))


class APIKeys(BaseModel):
    """Third-party OSINT API keys. All optional — modules auto-disable without keys."""
    hibp: Optional[str] = Field(default=None, description="HaveIBeenPwned API key")
    intelx: Optional[str] = Field(default=None, description="Intelligence X API key")
    dehashed: Optional[str] = Field(default=None, description="DeHashed API key")
    leaklookup: Optional[str] = Field(default=None, description="Leak-Lookup API key")
    shodan: Optional[str] = Field(default=None, description="Shodan API key")
    virustotal: Optional[str] = Field(default=None, description="VirusTotal API key")
    hibp_keys: List[str] = Field(default_factory=list, description="Rotating HIBP API-key pool")
    intelx_keys: List[str] = Field(default_factory=list, description="Rotating Intelligence X API-key pool")

    def pool(self, provider: str) -> List[str]:
        primary = getattr(self, provider, None)
        configured = list(getattr(self, f"{provider}_keys", []) or [])
        return list(dict.fromkeys(([primary] if primary else []) + [key for key in configured if key]))


class Neo4jSettings(BaseModel):
    """Neo4j graph database configuration."""
    uri: str = Field(default="bolt://localhost:7687")
    user: str = Field(default="neo4j")
    password: str = Field(default="password")
    enabled: bool = Field(default=False, description="Use Neo4j instead of NetworkX")


class HTTPSettings(BaseModel):
    """HTTP client resilience configuration."""
    timeout: int = Field(default=30, description="Request timeout in seconds")
    max_retries: int = Field(default=3, description="Retry attempts with backoff")
    ssl_verify: bool = Field(default=True, description="Verify remote TLS certificates")
    user_agents: List[str] = Field(default_factory=lambda: [
        # Chrome (Windows, Mac, Linux)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        # Firefox (Windows, Mac, Linux)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
        # Safari (Mac, iOS, iPad)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        # Edge (Windows, Mac)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        # Opera
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 OPR/111.0.0.0",
        # Android
        "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
        # Brave
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Brave/125",
        # Vivaldi
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Vivaldi/6.7",
        # Firefox ESR
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
    ])


class Settings(BaseSettings):
    """Main application settings. Composes all sub-configurations.

    Environment variables override defaults. Supports .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Application
    app_name: str = "Rahasya OSINT Platform"
    environment: str = Field(default="development")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # Sub-settings
    db: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    celery: CelerySettings = CelerySettings()
    tor: TorSettings = TorSettings()
    scan: ScanSettings = ScanSettings()
    api_keys: APIKeys = APIKeys()
    neo4j: Neo4jSettings = Neo4jSettings()
    http: HTTPSettings = HTTPSettings()
    storage: StorageSettings = StorageSettings()


# Global singleton
settings = Settings()
