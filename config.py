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

    # Research / Scraping
    FIRECRAWL_API_KEY = os.environ.get('FIRECRAWL_API_KEY')
    SERPAPI_API_KEY = os.environ.get('SERPAPI_API_KEY')
    # Optional residential proxy for YouTube transcripts (YouTube blocks Heroku IPs)
    WEBSHARE_PROXY_USERNAME = os.environ.get('WEBSHARE_PROXY_USERNAME')
    WEBSHARE_PROXY_PASSWORD = os.environ.get('WEBSHARE_PROXY_PASSWORD')
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')

    # Discovery max-age filter: drop candidates whose published date is older
    # than this many days. Items without a parseable date are kept.
    RESEARCH_RECENCY_DAYS = int(os.environ.get('RESEARCH_RECENCY_DAYS', 30))

    # n8n Workflow Triggers
    N8N_CONTENT_WORKFLOW_URL = os.environ.get('N8N_CONTENT_WORKFLOW_URL')
    N8N_IMAGE_WORKFLOW_URL = os.environ.get('N8N_IMAGE_WORKFLOW_URL')
    N8N_AUDIT_WORKFLOW_URL = os.environ.get('N8N_AUDIT_WORKFLOW_URL')
    N8N_SUBMIT_URL_WORKFLOW_URL = os.environ.get('N8N_SUBMIT_URL_WORKFLOW_URL')
    N8N_CANDIDATE_CONTENT_WORKFLOW_URL = os.environ.get('N8N_CANDIDATE_CONTENT_WORKFLOW_URL')
    N8N_RECIPE_WORKFLOW_URL = os.environ.get('N8N_RECIPE_WORKFLOW_URL')

    # Celery Configuration
    _redis_url = os.environ.get('REDIS_URL') or os.environ.get('CELERY_BROKER_URL') or 'redis://localhost:6379/0'
    # Heroku Redis uses rediss:// (SSL) - add required SSL params
    if _redis_url.startswith('rediss://'):
        _redis_url = _redis_url + '?ssl_cert_reqs=CERT_NONE'
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url

    # Enrichment
    ENRICHMENT_MIN_SCORE = float(os.environ.get('ENRICHMENT_MIN_SCORE', 25.0))
    ENRICHMENT_MAX_PER_RUN = int(os.environ.get('ENRICHMENT_MAX_PER_RUN', 50))

    # LLM Triage
    TRIAGE_ENABLED = os.environ.get('TRIAGE_ENABLED', 'true').lower() == 'true'
    TRIAGE_MODEL = os.environ.get('TRIAGE_MODEL', 'claude-haiku-4-5-20251001')
    TRIAGE_MAX_BATCH_SIZE = int(os.environ.get('TRIAGE_MAX_BATCH_SIZE', 40))
    TRIAGE_FETCH_BUDGET = int(os.environ.get('TRIAGE_FETCH_BUDGET', 5))

    # Source config assistant (admin UI helper for authoring source config JSON)
    CONFIG_ASSISTANT_MODEL = os.environ.get('CONFIG_ASSISTANT_MODEL', 'claude-opus-5')

    # Data source report analysis (PDF -> story angles); per-source claude_model overrides
    DATA_SCRAPER_MODEL = os.environ.get('DATA_SCRAPER_MODEL', 'claude-opus-5')

    # Editor-requested AI revisions of FINAL article versions
    REVISION_MODEL = os.environ.get('REVISION_MODEL', 'claude-opus-5')

    # Mailgun email notifications
    MAILGUN_API_KEY = os.environ.get('MAILGUN_API_KEY')
    MAILGUN_DOMAIN = os.environ.get('MAILGUN_DOMAIN')
    MAILGUN_FROM = os.environ.get('MAILGUN_FROM', 'WATT Automation <noreply@mg.example.com>')

    # Pagination
    ITEMS_PER_PAGE = 20