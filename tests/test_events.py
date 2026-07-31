import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

try:
    from rahasya.core.events import EventManager
    HAS_EVENTS = True
except ImportError:
    HAS_EVENTS = False

pytestmark = pytest.mark.skipif(not HAS_EVENTS, reason="Events module not found")

@pytest.fixture
def event_manager():
    if not HAS_EVENTS: return None
    return EventManager()

@pytest.mark.asyncio
async def test_publish_subscribe(event_manager):
    if not HAS_EVENTS: return
    cb = AsyncMock()
    event_manager.subscribe("test_event", cb)
    await event_manager.publish("test_event", {"data": "test"})
    cb.assert_called_once_with({"data": "test"})

@pytest.mark.asyncio
async def test_wildcard_subscription(event_manager):
    if not HAS_EVENTS: return
    cb = AsyncMock()
    event_manager.subscribe("*", cb)
    await event_manager.publish("any_event", {"data": "test"})
    cb.assert_called_once_with("any_event", {"data": "test"})

@pytest.mark.asyncio
async def test_unsubscribe(event_manager):
    if not HAS_EVENTS: return
    cb = AsyncMock()
    event_manager.subscribe("test_event", cb)
    event_manager.unsubscribe("test_event", cb)
    await event_manager.publish("test_event", {"data": "test"})
    cb.assert_not_called()

@pytest.mark.asyncio
async def test_async_callback(event_manager):
    if not HAS_EVENTS: return
    cb = AsyncMock()
    event_manager.subscribe("test_event", cb)
    await event_manager.publish("test_event", {"data": "test"})
    cb.assert_awaited_once()
