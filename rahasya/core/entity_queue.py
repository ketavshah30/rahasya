import json
import heapq
import asyncio
from typing import Optional, Any
from loguru import logger

from rahasya.core.models import Entity, SourceReliability

try:
    import redis.asyncio as redis
except ImportError:
    redis = None


class EntityQueue:
    """Redis-backed priority queue for entity processing with in-memory fallback."""
    
    def __init__(self, redis_url: Optional[str] = None, scan_id: str = "default"):
        self.scan_id = scan_id
        self._queue_key = f"rahasya:queue:{scan_id}"
        self._visited_key = f"rahasya:visited:{scan_id}"
        
        self.redis_client = None
        if redis is not None and redis_url:
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
            logger.info(f"Initialized Redis EntityQueue for scan_id={scan_id}")
        else:
            self._mem_queue = []
            self._mem_visited = set()
            self._mem_lock = asyncio.Lock()
            logger.info(f"Initialized In-Memory EntityQueue for scan_id={scan_id}")
            
        self.source_reliability_weight = {
            SourceReliability.HIGH: 1.0,
            SourceReliability.MEDIUM: 0.75,
            SourceReliability.LOW: 0.5,
            SourceReliability.UNVERIFIED: 0.25
        }

    def _calculate_priority(self, entity: Entity) -> float:
        weight = self.source_reliability_weight.get(entity.source_reliability, 0.25)
        # Higher score means higher priority. Since Redis sorted sets pop lowest first by default,
        # we can invert it or use ZREVRANGE. Let's make priority positive and use max priority.
        return entity.confidence * weight

    async def enqueue(self, entity: Entity) -> bool:
        """Add an entity to the queue if not already visited."""
        priority = self._calculate_priority(entity)
        dedup_key = f"{entity.entity_type.value}:{entity.normalized_value}"
        
        if self.redis_client:
            is_visited = await self.redis_client.sismember(self._visited_key, dedup_key)
            if is_visited:
                return False
            
            await self.redis_client.sadd(self._visited_key, dedup_key)
            # Store JSON payload in sorted set
            payload = entity.model_dump_json()
            await self.redis_client.zadd(self._queue_key, {payload: priority})
            return True
        else:
            async with self._mem_lock:
                if dedup_key in self._mem_visited:
                    return False
                self._mem_visited.add(dedup_key)
                # Invert priority for heapq (min-heap)
                heapq.heappush(self._mem_queue, (-priority, entity))
                return True

    async def dequeue(self) -> Optional[Entity]:
        """Pop the highest priority entity from the queue."""
        if self.redis_client:
            # Get highest score element
            result = await self.redis_client.zpopmax(self._queue_key, 1)
            if not result:
                return None
            payload, score = result[0]
            try:
                return Entity.model_validate_json(payload)
            except Exception as e:
                logger.error(f"Failed to parse entity from queue: {e}")
                return None
        else:
            async with self._mem_lock:
                if not self._mem_queue:
                    return None
                priority_inv, entity = heapq.heappop(self._mem_queue)
                return entity

    async def peek(self) -> Optional[Entity]:
        """View the highest priority entity without removing it."""
        if self.redis_client:
            result = await self.redis_client.zrevrange(self._queue_key, 0, 0)
            if not result:
                return None
            try:
                return Entity.model_validate_json(result[0])
            except Exception as e:
                logger.error(f"Failed to parse entity in peek: {e}")
                return None
        else:
            async with self._mem_lock:
                if not self._mem_queue:
                    return None
                return self._mem_queue[0][1]

    async def size(self) -> int:
        """Get current queue size."""
        if self.redis_client:
            return await self.redis_client.zcard(self._queue_key)
        else:
            async with self._mem_lock:
                return len(self._mem_queue)

    async def is_empty(self) -> bool:
        """Check if queue is empty."""
        return (await self.size()) == 0

    async def clear(self) -> None:
        """Clear the queue and visited set."""
        if self.redis_client:
            await self.redis_client.delete(self._queue_key, self._visited_key)
        else:
            async with self._mem_lock:
                self._mem_queue.clear()
                self._mem_visited.clear()
