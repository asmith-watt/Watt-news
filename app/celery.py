from celery import Celery
from celery.schedules import crontab

celery = Celery('wattautomation')


def make_celery(app):
    """Create and configure Celery instance with Flask app context."""
    celery.conf.update(
        broker_url=app.config['CELERY_BROKER_URL'],
        result_backend=app.config['CELERY_RESULT_BACKEND'],
        task_serializer='json',
        result_serializer='json',
        accept_content=['json'],
        timezone='UTC',
        enable_utc=True,
        task_track_started=True,
        task_time_limit=30 * 60,  # 30 minutes max per task
    )

    # Configure beat schedule for periodic tasks
    celery.conf.beat_schedule = {
        'check-publication-schedules': {
            'task': 'app.tasks.check_publication_schedules',
            'schedule': 60.0,  # Every 60 seconds
        },
        'check-candidate-content-schedules': {
            'task': 'app.tasks.check_candidate_content_schedules',
            'schedule': 60.0,  # Every 60 seconds
        },
        'check-research-schedules': {
            'task': 'app.tasks.check_research_schedules',
            'schedule': 3600.0,  # Every hour
        },
        'generate-weekly-briefings': {
            'task': 'app.tasks.generate_weekly_briefings',
            'schedule': crontab(day_of_week=0, hour=8, minute=0),  # Mondays 8 AM UTC
        },
    }

    class ContextTask(celery.Task):
        """Wrap tasks with Flask app context for database access."""
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
