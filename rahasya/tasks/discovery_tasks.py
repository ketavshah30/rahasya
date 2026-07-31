"""Tasks for executing individual OSINT discovery modules."""
import asyncio
import uuid
import time
from typing import Dict, Any
from loguru import logger
from celery.exceptions import MaxRetriesExceededError

from rahasya.celery_app import app
from rahasya.storage.repository import EntityRepository
from rahasya.storage.sql_models import ModuleExecutionStatus


async def _async_run_module(scan_id_str: str, module_name: str, entity_dict: Dict[str, Any]) -> None:
    """Simulates async execution of an OSINT module."""
    # In reality, this would dynamically load and execute a specific module class
    # and create a ModuleExecutionRecord.
    scan_id = uuid.UUID(scan_id_str)
    logger.info(f"Running module {module_name} on entity {entity_dict.get('value')} for scan {scan_id}")
    
    # Simulate some async work
    await asyncio.sleep(0.5)
    
    # Store mocked results...
    logger.debug(f"Module {module_name} completed.")


@app.task(bind=True, max_retries=2)
def run_module(self, scan_id: str, module_name: str, entity_dict: Dict[str, Any]) -> None:
    """Execute a single module on a specific entity."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    try:
        loop.run_until_complete(_async_run_module(scan_id, module_name, entity_dict))
    except Exception as exc:
        logger.error(f"Module {module_name} failed: {exc}")
        try:
            self.retry(exc=exc, countdown=2 ** self.request.retries)
        except MaxRetriesExceededError:
            logger.error(f"Max retries exceeded for {module_name}")


@app.task
def run_social_discovery(scan_id: str, entity_dict: Dict[str, Any]) -> None:
    """Fan out to all social media related modules."""
    modules = ['twitter_search', 'linkedin_profile', 'instagram_lookup']
    for mod in modules:
        app.send_task('rahasya.tasks.discovery_tasks.run_module', args=[scan_id, mod, entity_dict])


@app.task
def run_breach_discovery(scan_id: str, entity_dict: Dict[str, Any]) -> None:
    """Fan out to all data breach related modules."""
    if entity_dict.get('type') in ('email', 'phone', 'username'):
        modules = ['haveibeenpwned', 'dehashed_search']
        for mod in modules:
            app.send_task('rahasya.tasks.discovery_tasks.run_module', args=[scan_id, mod, entity_dict])


@app.task
def run_darkweb_discovery(scan_id: str, entity_dict: Dict[str, Any]) -> None:
    """Fan out to all darkweb intelligence modules."""
    modules = ['ahmia_search', 'tor_forums']
    for mod in modules:
        app.send_task('rahasya.tasks.discovery_tasks.run_module', args=[scan_id, mod, entity_dict])
