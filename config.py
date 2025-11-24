import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'postgresql://wattuser:wattnews11@localhost/wattautomation'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # API Configuration
    N8N_API_KEY = os.environ.get('N8N_API_KEY')
    CMS_API_URL = os.environ.get('CMS_API_URL')
    CMS_API_KEY = os.environ.get('CMS_API_KEY')

    # Pagination
    ITEMS_PER_PAGE = 20