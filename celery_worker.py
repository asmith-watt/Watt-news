"""
Celery worker entry point for Heroku.
Usage:
    celery -A celery_worker:celery worker --loglevel=info
    celery -A celery_worker:celery beat --loglevel=info
"""
from app import create_app
from app.celery import make_celery

# Create Flask app and configure Celery
app = create_app()
celery = make_celery(app)

# Import tasks to register them with Celery
from app import tasks  # noqa: F401, E402
