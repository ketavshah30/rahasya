"""Celery application factory for Rahasya task orchestration.

Configures the Celery app with Redis broker, queues, routing, and task modules.
"""
from celery import Celery
from loguru import logger

from rahasya.config import Settings

settings = Settings()

# Create Celery application
app = Celery(
    'rahasya',
    broker=settings.celery.broker_url.unicode_string() if settings.celery.broker_url else 'redis://localhost:6379/0',
    backend=settings.celery.result_backend.unicode_string() if settings.celery.result_backend else 'redis://localhost:6379/0',
    include=[
        'rahasya.tasks.scan_tasks',
        'rahasya.tasks.discovery_tasks'
    ]
)

# Configure Celery
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    
    # Task routing configuration
    task_routes={
        'rahasya.tasks.scan_tasks.*': {'queue': 'orchestration'},
        'rahasya.tasks.discovery_tasks.*': {'queue': 'discovery'},
        'rahasya.tasks.discovery_tasks.run_social_discovery': {'queue': 'social'},
        'rahasya.tasks.discovery_tasks.run_breach_discovery': {'queue': 'breach'},
        'rahasya.tasks.discovery_tasks.run_darkweb_discovery': {'queue': 'darkweb'},
    },
    
    # Default routing
    task_default_queue='default',
    
    # Result expiration (1 day)
    result_expires=86400,
    
    # Prefetch limits and concurrency based on settings can be added here
    worker_prefetch_multiplier=1,
    
    # Error handling
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Optional Celery Beat schedule for periodic tasks
app.conf.beat_schedule = {
    # Example periodic cleanup task
    # 'cleanup-old-scans': {
    #     'task': 'rahasya.tasks.system_tasks.cleanup_old_scans',
    #     'schedule': 86400.0, # Every 24 hours
    # },
}

@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    """Setup periodic tasks for Celery Beat."""
    logger.info("Celery beat schedule configured.")

if __name__ == '__main__':
    app.start()
