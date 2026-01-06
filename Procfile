release: flask db upgrade
web: gunicorn run:app
worker: celery -A celery_worker:celery worker --loglevel=info
clock: celery -A celery_worker:celery beat --loglevel=info