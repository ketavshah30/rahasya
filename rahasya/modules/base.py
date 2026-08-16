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
from rahasya.config import Settings, settings
from rahasya.utils.logging import get_logger
from rahasya.utils.http_client import StealthHTTPClient, TorHTTPClient
from rahasya.storage.network_audit import audit_scope, record_audit_event


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

    def __init__(self, config: Optional[Settings] = None):
        """Initialize module with application configuration.

        Args:
            config: Application-wide Settings instance.
        """
        self.config = config or settings
        self.logger = get_logger(f"module.{self.name}")
        self.http_client: Optional[StealthHTTPClient] = None
        self._initialized = False
        self._api_key_index = 0

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
                ssl_verify=self.config.http.ssl_verify,
            )
        elif self.http_client is None:
            self.http_client = StealthHTTPClient(
                timeout=float(self.config.http.timeout),
                max_retries=self.config.http.max_retries,
                ssl_verify=self.config.http.ssl_verify,
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
        aliases = {"intelligencex": "intelx"}
        key_name = aliases.get(key_name, key_name)
        pool = self.config.api_keys.pool(key_name)
        return pool[self._api_key_index % len(pool)] if pool else None

    def rotate_api_key(self) -> Optional[str]:
        """Advance to the next configured provider key after quota/rate limiting."""
        self._api_key_index += 1
        return self._get_api_key()

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

    async def safe_execute(self, entity: Entity, scan_id: str = "manual") -> List[Entity]:
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
        entity_type_value = getattr(getattr(entity, "entity_type", None), "value", "unknown")
        entity_value = getattr(entity, "value", "")
        with audit_scope(scan_id, self.name, self.config.storage.scan_dir):
            if not self.is_available():
                self.logger.debug(f"Skipping {self.name}: not available")
                record_audit_event(
                    "module_skipped",
                    outcome="skipped",
                    entity_type=entity_type_value,
                    entity_value=entity_value,
                    message="Module prerequisites are not available",
                )
                return []

            if not self._initialized:
                try:
                    await self.setup()
                except Exception as exc:
                    record_audit_event(
                        "module_setup_failed",
                        outcome="failed",
                        entity_type=entity_type_value,
                        entity_value=entity_value,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self.logger.error(f"Module {self.name} setup failed: {exc}")
                    return []

            start_time = time.monotonic()
            accepted = getattr(self, "accepts", getattr(self, "supported_entity_types", []))
            produced = getattr(self, "produces", [])
            record_audit_event(
                "module_started",
                outcome="started",
                entity_type=entity_type_value,
                entity_value=entity_value,
                accepts=[item.value for item in accepted],
                produces=[item.value for item in produced],
            )
            try:
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
                record_audit_event(
                    "module_completed",
                    outcome="success" if results else "no_results",
                    duration_ms=round(duration_ms, 2),
                    result_count=len(results),
                    entity_type=entity_type_value,
                    entity_value=entity_value,
                )
                return results

            except asyncio.CancelledError:
                duration_ms = (time.monotonic() - start_time) * 1000
                self.logger.warning(f"Module {self.name} was cancelled")
                record_audit_event(
                    "module_cancelled",
                    outcome="cancelled",
                    duration_ms=round(duration_ms, 2),
                    entity_type=entity_type_value,
                    entity_value=entity_value,
                    message="Module task was cancelled, commonly by timeout or scan cancellation",
                )
                raise
            except Exception as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                self.logger.error(
                    f"Module {self.name} failed after {duration_ms:.0f}ms: "
                    f"{type(e).__name__}: {e}"
                )
                record_audit_event(
                    "module_failed",
                    outcome="failed",
                    duration_ms=round(duration_ms, 2),
                    entity_type=entity_type_value,
                    entity_value=entity_value,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                return []
