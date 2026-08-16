"""Tasks for orchestrating a complete OSINT scan lifecycle."""
import asyncio
import uuid
from typing import Any, Dict
from loguru import logger
from celery.exceptions import MaxRetriesExceededError

from rahasya.celery_app import app
from rahasya.core.models import ScanRequest, ScanStatus, Entity
from rahasya.storage.repository import ScanRepository, EntityRepository


@app.task(
    bind=True,
    name="rahasya.tasks.scan_tasks.execute_scan",
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def execute_scan(self, scan_id: str, request_data: Dict[str, Any]) -> str:
    """Run the durable orchestrator lifecycle in a Celery worker."""
    from rahasya.dashboard.state import _background_worker

    _background_worker(scan_id, request_data)
    return scan_id


async def _async_start_scan(scan_request_dict: Dict[str, Any]) -> str:
    """Async implementation of starting a scan."""
    repo = ScanRepository()
    
    # In a real system, you would parse the dict to the ScanRequest model
    # req = ScanRequest(**scan_request_dict)
    
    scan = await repo.create_scan(
        request_data=scan_request_dict,
        config=scan_request_dict.get('config', {})
    )
    
    await repo.update_scan_status(scan.id, ScanStatus.RUNNING)
    
    # Enqueue initial targets
    targets = scan_request_dict.get('targets', [])
    for target in targets:
        # Create entity and dispatch to processing
        entity_repo = EntityRepository()
        entity_data = {
            'type': target.get('type', 'unknown'),
            'value': target.get('value', ''),
            'source_module': 'user_input',
            'depth': 0
        }
        entity = await entity_repo.create_entity(scan.id, entity_data)
        
        # Dispatch process task
        app.send_task(
            'rahasya.tasks.scan_tasks.process_entity',
            args=[str(scan.id), str(entity.id)]
        )
        
    return str(scan.id)


@app.task(bind=True, max_retries=3, default_retry_delay=5)
def start_scan(self, scan_request_dict: Dict[str, Any]) -> str:
    """Start a new OSINT scan based on the provided request."""
    logger.info(f"Starting scan for targets: {scan_request_dict.get('targets', [])}")
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    try:
        scan_id = loop.run_until_complete(_async_start_scan(scan_request_dict))
        return scan_id
    except Exception as exc:
        logger.error(f"Failed to start scan: {exc}")
        try:
            self.retry(exc=exc)
        except MaxRetriesExceededError:
            logger.error("Max retries exceeded for start_scan.")
            raise


async def _async_process_entity(scan_id_str: str, entity_id_str: str) -> None:
    """Async implementation of entity processing."""
    scan_id = uuid.UUID(scan_id_str)
    entity_id = uuid.UUID(entity_id_str)
    
    repo = EntityRepository()
    entity = await repo.get_entity(entity_id)
    if not entity:
        logger.warning(f"Entity {entity_id} not found for processing.")
        return
        
    logger.info(f"Processing entity: {entity.value} (Type: {entity.entity_type})")
    
    # Based on entity type and depth, dispatch discovery modules
    entity_dict = {
        'id': str(entity.id),
        'type': entity.entity_type,
        'value': entity.value,
        'depth': entity.depth
    }
    
    # Fan out to different discovery pipelines based on configuration
    app.send_task('rahasya.tasks.discovery_tasks.run_social_discovery', args=[scan_id_str, entity_dict])
    app.send_task('rahasya.tasks.discovery_tasks.run_breach_discovery', args=[scan_id_str, entity_dict])
    app.send_task('rahasya.tasks.discovery_tasks.run_darkweb_discovery', args=[scan_id_str, entity_dict])


@app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_entity(self, scan_id: str, entity_id: str) -> None:
    """Process a discovered entity and dispatch relevant modules."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    try:
        loop.run_until_complete(_async_process_entity(scan_id, entity_id))
    except Exception as exc:
        logger.error(f"Error processing entity {entity_id}: {exc}")
        self.retry(exc=exc, countdown=2 ** self.request.retries)


async def _async_complete_scan(scan_id_str: str) -> None:
    """Finalize a scan."""
    scan_id = uuid.UUID(scan_id_str)
    repo = ScanRepository()
    await repo.update_scan_status(scan_id, ScanStatus.COMPLETED)
    logger.info(f"Scan {scan_id} marked as COMPLETED.")


@app.task(bind=True, max_retries=3)
def complete_scan(self, scan_id: str) -> None:
    """Complete a scan and compute final statistics."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    try:
        loop.run_until_complete(_async_complete_scan(scan_id))
    except Exception as exc:
        logger.error(f"Error completing scan {scan_id}: {exc}")
        self.retry(exc=exc, countdown=2 ** self.request.retries)


async def _async_cancel_scan(scan_id_str: str) -> None:
    """Cancel a running scan."""
    scan_id = uuid.UUID(scan_id_str)
    repo = ScanRepository()
    await repo.update_scan_status(scan_id, ScanStatus.CANCELLED, error_message="Cancelled by user.")
    logger.info(f"Scan {scan_id} marked as CANCELLED.")


@app.task(bind=True, max_retries=3)
def cancel_scan(self, scan_id: str) -> None:
    """Cancel a running scan and halt ongoing tasks."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    try:
        loop.run_until_complete(_async_cancel_scan(scan_id))
    except Exception as exc:
        logger.error(f"Error cancelling scan {scan_id}: {exc}")
        self.retry(exc=exc)
