import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'

    # Get DATABASE_URL and fix Heroku's postgres:// to postgresql://
    database_url = os.environ.get('DATABASE_URL') or \
        'postgresql://wattuser:wattnews11@localhost/wattautomation'
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

    SQLALCHEMY_DATABASE_URI = database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # API Configuration
    N8N_API_KEY = os.environ.get('N8N_API_KEY')
    CMS_API_URL = os.environ.get('CMS_API_URL')
    CMS_API_KEY = os.environ.get('CMS_API_KEY')

    # n8n Workflow Triggers
    N8N_CONTENT_WORKFLOW_URL = os.environ.get('N8N_CONTENT_WORKFLOW_URL')
    N8N_IMAGE_WORKFLOW_URL = os.environ.get('N8N_IMAGE_WORKFLOW_URL')
    N8N_AUDIT_WORKFLOW_URL = os.environ.get('N8N_AUDIT_WORKFLOW_URL')
    N8N_SUBMIT_URL_WORKFLOW_URL = os.environ.get('N8N_SUBMIT_URL_WORKFLOW_URL')

    # Celery Configuration
    _redis_url = os.environ.get('REDIS_URL') or os.environ.get('CELERY_BROKER_URL') or 'redis://localhost:6379/0'
    # Heroku Redis uses rediss:// (SSL) - add required SSL params
    if _redis_url.startswith('rediss://'):
        _redis_url = _redis_url + '?ssl_cert_reqs=CERT_NONE'
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url

    # Pagination
    ITEMS_PER_PAGE = 20