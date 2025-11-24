# News Staging Web Application

A Flask-based web application that sits between n8n automation and CMS systems, allowing editors to review and approve news content before publishing.

## Features

- User authentication with role-based access control
- Multi-publication support with isolated content
- News content staging and approval workflow
- API endpoints for n8n integration
- CMS integration for content publishing
- News source management per publication
- Admin interface for managing publications, users, and sources
- Responsive UI with Tailwind CSS

## Architecture

```
n8n → [API] → News Staging App → [CMS API] → CMS System
         ↑                   ↓
    News Sources      Editor Dashboard
```

## Installation

### Local Development

1. Clone the repository:
```bash
git clone <repository-url>
cd wattAutomation
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Initialize the database:
```bash
flask db init
flask db migrate -m "Initial migration"
flask db upgrade
```

6. Create an admin user (Python shell):
```bash
flask shell
```
```python
from app import db
from app.models import User, Role

# Create admin role
admin_role = Role(name='admin', description='Administrator')
db.session.add(admin_role)

# Create admin user
admin = User(username='admin', email='admin@example.com')
admin.set_password('your-password')
admin.roles.append(admin_role)
db.session.add(admin)

db.session.commit()
```

7. Run the development server:
```bash
python run.py
```

Visit `http://localhost:5000` in your browser.

### Heroku Deployment

1. Create a Heroku app:
```bash
heroku create your-app-name
```

2. Add PostgreSQL:
```bash
heroku addons:create heroku-postgresql:mini
```

3. Set environment variables:
```bash
heroku config:set SECRET_KEY=your-secret-key
heroku config:set N8N_API_KEY=your-n8n-api-key
heroku config:set FLASK_ENV=production
```

4. Deploy:
```bash
git push heroku main
```

5. Run database migrations:
```bash
heroku run flask db upgrade
```

6. Create admin user:
```bash
heroku run flask shell
# Then run the user creation commands from step 6 above
```

## API Documentation

### Authentication

All API endpoints require authentication via the `X-API-Key` header:
```
X-API-Key: your-n8n-api-key
```

### Endpoints

#### Create News Content
```
POST /api/news
Content-Type: application/json

{
  "publication_id": 1,
  "title": "News Title",
  "content": "Full article content",
  "summary": "Brief summary",
  "author": "Author Name",
  "source_url": "https://source.com/article",
  "source_name": "Source Name",
  "image_url": "https://example.com/image.jpg",
  "published_date": "2024-01-01T12:00:00",
  "metadata": {"key": "value"}
}
```

#### Bulk Create News Content
```
POST /api/news/bulk
Content-Type: application/json

[
  {
    "publication_id": 1,
    "title": "News Title 1",
    ...
  },
  {
    "publication_id": 1,
    "title": "News Title 2",
    ...
  }
]
```

#### Get News Sources for Publication
```
GET /api/sources/{publication_id}
```

#### List Publications
```
GET /api/publications
```

## User Roles

- **Admin**: Full access to all features, manage publications, users, and sources
- **Editor**: View and manage news content for their assigned publication

## Database Models

- **User**: User accounts with authentication
- **Role**: User roles for permission management
- **Publication**: Organization concept grouping content and sources
- **NewsSource**: Configured news sources for n8n to query
- **NewsContent**: Staged news articles awaiting review

## Development

### Project Structure
```
wattAutomation/
├── app/
│   ├── __init__.py          # App factory
│   ├── models.py            # Database models
│   ├── forms.py             # WTForms
│   ├── routes/              # Route blueprints
│   │   ├── auth.py          # Authentication
│   │   ├── main.py          # Main dashboard
│   │   ├── api.py           # API endpoints
│   │   └── admin.py         # Admin interface
│   └── templates/           # Jinja2 templates
├── config.py                # Configuration
├── run.py                   # Application entry point
├── requirements.txt         # Python dependencies
└── Procfile                 # Heroku configuration
```

### Adding New Features

1. Create models in `app/models.py`
2. Create forms in `app/forms.py`
3. Add routes in appropriate blueprint
4. Create templates in `app/templates/`
5. Run migrations: `flask db migrate -m "Description"` and `flask db upgrade`

## Configuration

Key environment variables:

- `SECRET_KEY`: Flask secret key for sessions
- `DATABASE_URL`: PostgreSQL connection string
- `N8N_API_KEY`: API key for n8n authentication
- `CMS_API_URL`: Default CMS API endpoint
- `CMS_API_KEY`: Default CMS API key

## License

MIT