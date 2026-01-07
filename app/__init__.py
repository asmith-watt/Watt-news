from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from markupsafe import Markup
import markdown
from config import Config

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'

    # Initialize Celery with app context
    from app.celery import make_celery
    make_celery(app)

    from app.auth import bp as auth_bp
    from app.main import bp as main_bp
    from app.api import bp as api_bp
    from app.admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)

    # Register CLI commands
    from app.cli import register_commands
    register_commands(app)

    # Register custom Jinja filters
    @app.template_filter('markdown')
    def markdown_filter(text):
        if text is None:
            return ''
        return Markup(markdown.markdown(text, extensions=['nl2br', 'fenced_code', 'tables']))

    return app


from app import models