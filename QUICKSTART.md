# Quick Start Guide

## Setup (5 minutes)

1. **Install dependencies**
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. **Configure environment**
```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
- `SECRET_KEY` - Generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
- `DATABASE_URL` - Your PostgreSQL connection string
- `N8N_API_KEY` - API key for n8n to authenticate

3. **Initialize database**
```bash
flask db init
flask db migrate -m "Initial migration"
flask db upgrade
python init_db.py  # Creates admin user interactively
```

4. **Run the app**
```bash
python run.py
```

Visit: http://localhost:5000

## First Steps

1. **Login** with your admin credentials
2. **Create a Publication** via Admin → Publications
3. **Add News Sources** for that publication
4. **Configure n8n** to push content to `/api/news`

## n8n Example Workflow

1. Get news sources: `GET /api/sources/{publication_id}`
2. Fetch news from those sources (your logic)
3. Push to staging: `POST /api/news` with news data

## Editor Workflow

1. Login to dashboard
2. View staged content
3. Review articles
4. Approve/Reject or Push directly to CMS

## API Testing

Test the API with curl:

```bash
# Create news content
curl -X POST http://localhost:5000/api/news \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-n8n-api-key" \
  -d '{
    "publication_id": 1,
    "title": "Test Article",
    "content": "This is a test article",
    "summary": "Test summary"
  }'

# Get news sources
curl http://localhost:5000/api/sources/1 \
  -H "X-API-Key: your-n8n-api-key"
```

## Heroku Deployment

```bash
heroku create your-app-name
heroku addons:create heroku-postgresql:mini
heroku config:set SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
heroku config:set N8N_API_KEY=your-key
git push heroku main
heroku run flask db upgrade
heroku run python init_db.py
heroku open
```

## Troubleshooting

**Database errors**: Make sure PostgreSQL is running and DATABASE_URL is correct

**Import errors**: Activate virtual environment and reinstall: `pip install -r requirements.txt`

**Login fails**: Check that you ran `init_db.py` to create the admin user

**API 401 errors**: Verify X-API-Key header matches N8N_API_KEY in .env