release: flask db upgrade
web: gunicorn run:app
worker: celery -A celery_worker:celery worker --loglevel=info --concurrency=2 --max-memory-per-child=250000 --without-gossip --without-mingle --without-heartbeat
clock: celery -A celery_worker:celery beat --loglevel=info