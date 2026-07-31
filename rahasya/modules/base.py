"""Abstract base class for all OSINT discovery modules.

Every discovery module must inherit from BaseModule and implement
the execute() method. The base class provides:
- Automatic HTTP client initialization with anti-detection
- Rate limiting enforcement
- Error handling and timing metrics
- Availability checks for optional dependencies
"""

import time
import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional, ClassVar

from rahasya.core.models import Entity, EntityType, SourceReliability
from rahasya.config import Settings
from rahasya.utils.logging import get_logger
from rahasya.utils.http_client import StealthHTTPClient, TorHTTPClient


class BaseModule(ABC):
    """Abstract base class for OSINT discovery modules.

    Subclasses must define class-level attributes and implement execute().
    """

    name: ClassVar[str]
    description: ClassVar[str]
    version: ClassVar[str] = "1.0.0"
    accepts: ClassVar[List[EntityType]]
    produces: ClassVar[List[EntityType]]
    requires_api_key: ClassVar[bool] = False
    requires_tor: ClassVar[bool] = False
    rate_limit: ClassVar[float] = 1.0  # max requests per second

    def __init__(self, config: Settings):
        """Initialize module with application configuration.

        Args:
            config: Application-wide Settings instance.
        """
        self.config = config
        self.logger = get_logger(f"module.{self.name}")
        self.http_client: Optional[StealthHTTPClient] = None
        self._initialized = False

    async def setup(self) -> None:
        """Initialize HTTP client and verify module dependencies.

        Called once before first execution. Creates an appropriate
        HTTP client based on whether the module requires Tor.
        """
        if self._initialized:
            return

        if self.requires_tor and self.config.tor.enabled:
            tor_proxy = f"socks5h://127.0.0.1:{self.config.tor.socks_port}"
            self.http_client = TorHTTPClient(
                tor_proxy=tor_proxy,
                timeout=float(self.config.http.timeout),
            )
        else:
            self.http_client = StealthHTTPClient(
                timeout=float(self.config.http.timeout),
                max_retries=self.config.http.max_retries,
            )

        self._initialized = True
        self.logger.info(f"Module {self.name} v{self.version} initialized")

    async def teardown(self) -> None:
        """Release resources held by this module."""
        if self.http_client:
            await self.http_client.close()
            self.http_client = None
        self._initialized = False

    def is_available(self) -> bool:
        """Check whether this module can run given current configuration.

        Returns:
            True if all prerequisites are met.
        """
        if self.requires_api_key:
            api_key = self._get_api_key()
            if not api_key:
                self.logger.debug(
                    f"Module {self.name} unavailable: API key not configured"
                )
                return False

        if self.requires_tor and not self.config.tor.enabled:
            self.logger.debug(
                f"Module {self.name} unavailable: Tor not enabled"
            )
            return False

        return True

    def _get_api_key(self) -> Optional[str]:
        """Retrieve the API key for this module from configuration.

        Looks up the key by module name in config.api_keys.
        """
        key_name = self.name.lower().replace("-", "_").replace(" ", "_")
        return getattr(self.config.api_keys, key_name, None)

    @abstractmethod
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        """Core discovery logic.

        Args:
            entity: The entity to investigate.
            scan_id: UUID of the current scan for tracking.

        Returns:
            List of newly discovered Entity objects.
        """
        ...

    async def safe_execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        """Execute with error handling, rate limiting, and timing.

        This is the primary entry point called by the orchestrator.
        It wraps execute() with:
        - Availability check
        - Automatic setup if needed
        - Rate limiting delay
        - Comprehensive error handling
        - Execution time logging

        Args:
            entity: The entity to investigate.
            scan_id: UUID of the current scan.

        Returns:
            List of discovered entities, or empty list on failure.
        """
        if not self.is_available():
            self.logger.debug(f"Skipping {self.name}: not available")
            return []

        if not self._initialized:
            await self.setup()

        start_time = time.monotonic()
        try:
            # Enforce rate limiting
            if self.rate_limit > 0:
                delay = 1.0 / self.rate_limit
                await asyncio.sleep(delay)

            self.logger.info(
                f"Executing {self.name} on [{entity.entity_type.value}] "
                f"'{entity.value}' (scan={scan_id[:8]}...)"
            )

            results = await self.execute(entity, scan_id)

            duration_ms = (time.monotonic() - start_time) * 1000
            self.logger.info(
                f"Completed {self.name}: {len(results)} entities found "
                f"in {duration_ms:.0f}ms"
            )

            return results

        except asyncio.CancelledError:
            self.logger.warning(f"Module {self.name} was cancelled")
            raise
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self.logger.error(
                f"Module {self.name} failed after {duration_ms:.0f}ms: "
                f"{type(e).__name__}: {e}"
            )
            return []
