import asyncio
import json
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Union
from loguru import logger


class EventType(str, Enum):
    ENTITY_DISCOVERED = "ENTITY_DISCOVERED"
    RELATIONSHIP_CREATED = "RELATIONSHIP_CREATED"
    MODULE_STARTED = "MODULE_STARTED"
    MODULE_COMPLETED = "MODULE_COMPLETED"
    MODULE_FAILED = "MODULE_FAILED"
    SCAN_STARTED = "SCAN_STARTED"
    SCAN_PROGRESS = "SCAN_PROGRESS"
    SCAN_COMPLETED = "SCAN_COMPLETED"
    SCAN_FAILED = "SCAN_FAILED"


@dataclass
class Event:
    type: EventType
    payload: Dict[str, Any]
    source_module: str = "system"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus:
    """Production pub/sub event system."""
    
    def __init__(self, redis_url: str | None = None, redis_enabled: bool = False):
        self._subscribers: Dict[Union[EventType, str], List[Callable]] = {}
        self._lock = asyncio.Lock()
        self._redis_url = redis_url
        self._redis_enabled = redis_enabled
        self._redis = None

    async def subscribe(self, event_type: Union[EventType, str], callback: Callable):
        """Subscribe to a specific event type or '*' for all events."""
        async with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
                logger.debug(f"Subscribed {callback.__name__} to {event_type}")

    async def unsubscribe(self, event_type: Union[EventType, str], callback: Callable):
        """Unsubscribe a callback from an event type."""
        async with self._lock:
            if event_type in self._subscribers:
                if callback in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(callback)
                    logger.debug(f"Unsubscribed {callback.__name__} from {event_type}")

    async def publish(self, event: Event):
        """Publish an event to all subscribers."""
        callbacks = []
        async with self._lock:
            callbacks.extend(self._subscribers.get(event.type, []))
            callbacks.extend(self._subscribers.get("*", []))
            
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Error in event callback for {event.type}: {e}")

        if self._redis_enabled and self._redis_url:
            try:
                if self._redis is None:
                    from redis.asyncio import Redis

                    self._redis = Redis.from_url(self._redis_url)
                await self._redis.publish(
                    "rahasya.events",
                    json.dumps({
                        "type": event.type.value,
                        "payload": event.payload,
                        "source_module": event.source_module,
                        "timestamp": event.timestamp.isoformat(),
                    }, default=str),
                )
            except Exception as exc:
                logger.warning(f"Redis event publish failed: {exc}")

    async def close(self):
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
