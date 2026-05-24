from celery import Celery
from config.settings import settings

# Initialize Celery app instance with Redis as broker and backend
celery_app = Celery(
    "strike_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["tasks.celery_tasks"]
)

# Celery Configurations optimized for standard workflow
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1  # Prevent worker from pre-fetching multiple tasks
)
