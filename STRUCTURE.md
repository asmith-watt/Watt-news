# Application Structure

## New Blueprint-Based Architecture

The application now uses a modular blueprint structure where each feature has its own folder containing routes, forms, and related logic.

```
wattAutomation/
├── app/
│   ├── __init__.py              # App factory, registers all blueprints
│   ├── models.py                # Shared database models
│   │
│   ├── auth/                    # Authentication Blueprint
│   │   ├── __init__.py          # Blueprint definition
│   │   ├── routes.py            # Login, logout, register routes
│   │   └── forms.py             # LoginForm, RegistrationForm
│   │
│   ├── main/                    # Main Dashboard Blueprint
│   │   ├── __init__.py          # Blueprint definition
│   │   └── routes.py            # Dashboard, content views, CMS push
│   │
│   ├── api/                     # API Blueprint (for n8n)
│   │   ├── __init__.py          # Blueprint definition
│   │   └── routes.py            # REST API endpoints
│   │
│   ├── admin/                   # Admin Management Blueprint
│   │   ├── __init__.py          # Blueprint definition
│   │   ├── routes.py            # CRUD for publications, sources, users
│   │   └── forms.py             # PublicationForm, NewsSourceForm
│   │
│   └── templates/               # Jinja2 templates (organized by blueprint)
│       ├── base.html
│       ├── auth/
│       │   ├── login.html
│       │   └── register.html
│       ├── main/
│       │   ├── dashboard.html
│       │   └── content_detail.html
│       └── admin/
│           ├── index.html
│           ├── publications.html
│           ├── publication_form.html
│           ├── news_sources.html
│           ├── news_source_form.html
│           └── users.html
│
├── config.py                    # Application configuration
├── run.py                       # Entry point
└── requirements.txt             # Dependencies
```

## Blueprint Structure Explained

Each blueprint folder follows this pattern:

### `__init__.py`
- Creates the Blueprint instance
- Defines URL prefix (if any)
- Imports routes module

Example:
```python
from flask import Blueprint

bp = Blueprint('auth', __name__, url_prefix='/auth')

from app.auth import routes
```

### `routes.py`
- Contains all route handlers for the blueprint
- Imports the blueprint from `__init__.py`
- Implements business logic

### `forms.py` (if needed)
- WTForms specific to this blueprint
- Validation logic
- Custom validators

## Benefits of This Structure

1. **Modularity**: Each feature is self-contained
2. **Scalability**: Easy to add new blueprints
3. **Organization**: Related code stays together
4. **Maintainability**: Clear separation of concerns
5. **Team Collaboration**: Different developers can work on different blueprints

## Blueprint URLs

- **auth**: `/auth/login`, `/auth/logout`, `/auth/register`
- **main**: `/`, `/dashboard`, `/content/<id>`
- **api**: `/api/news`, `/api/sources/<id>`, `/api/publications`
- **admin**: `/admin`, `/admin/publications`, `/admin/users`

## Import Pattern

In `app/__init__.py`:
```python
from app.auth import bp as auth_bp
from app.main import bp as main_bp
from app.api import bp as api_bp
from app.admin import bp as admin_bp

app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(api_bp)
app.register_blueprint(admin_bp)
```

This structure makes the application more maintainable and follows Flask best practices for larger applications.