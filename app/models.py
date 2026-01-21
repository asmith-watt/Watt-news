from datetime import datetime
from enum import Enum
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db, login_manager


class SourceType(Enum):
    NEWS_SITE = "News Site"
    RSS_FEED = "RSS Feed"
    KEYWORD_SEARCH = "Keyword Search"
    DATA = "Data"
    HOUSE_CONTENT = "House Content"
    COMPETITOR = "Competitor"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.value) for choice in cls]

    @classmethod
    def values(cls):
        return [choice.value for choice in cls]


user_roles = db.Table('user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True)
)


user_publications = db.Table('user_publications',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('publication_id', db.Integer, db.ForeignKey('publication.id'), primary_key=True)
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    roles = db.relationship('Role', secondary=user_roles, backref=db.backref('users', lazy='dynamic'))
    publications = db.relationship('Publication', secondary=user_publications, backref=db.backref('users', lazy='dynamic'))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_role(self, role_name):
        return any(role.name == role_name for role in self.roles)

    def has_publication_access(self, publication_id):
        return any(pub.id == publication_id for pub in self.publications)

    def get_publication_ids(self):
        return [pub.id for pub in self.publications]

    def __repr__(self):
        return f'<User {self.username}>'


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(256))

    def __repr__(self):
        return f'<Role {self.name}>'


class Publication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    publication_domain = db.Column(db.String(128), unique=True, nullable=False, index=True)
    industry_description = db.Column(db.Text)
    reader_personas = db.Column(db.Text)
    reader_pain_points = db.Column(db.Text)
    access_api_key = db.Column(db.String(256))
    cms_url = db.Column(db.String(256))
    cms_api_key = db.Column(db.String(256))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Scheduling fields for automated content generation
    schedule_enabled = db.Column(db.Boolean, default=False)
    schedule_frequency = db.Column(db.String(32))  # 'daily' or 'weekly'
    schedule_time = db.Column(db.String(5))  # 'HH:MM' format (UTC)
    schedule_day_of_week = db.Column(db.Integer)  # 0-6 for weekly (Monday=0)
    last_scheduled_run = db.Column(db.DateTime)
    next_scheduled_run = db.Column(db.DateTime, index=True)

    news_sources = db.relationship('NewsSource', backref='publication', lazy='dynamic', cascade='all, delete-orphan')
    news_content = db.relationship('NewsContent', backref='publication', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Publication {self.name}>'


class NewsSource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    publication_id = db.Column(db.Integer, db.ForeignKey('publication.id'), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    source_type = db.Column(db.String(64))
    url = db.Column(db.String(512))
    keywords = db.Column(db.Text)
    config = db.Column(db.JSON)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<NewsSource {self.name}>'


class NewsContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    publication_id = db.Column(db.Integer, db.ForeignKey('publication.id'), nullable=False)
    title = db.Column(db.String(512), nullable=False)
    # Legacy content fields - kept for backward compatibility
    deck = db.Column(db.Text)
    teaser = db.Column(db.Text)
    content = db.Column(db.Text)
    summary = db.Column(db.Text)
    notes = db.Column(db.Text)
    author = db.Column(db.String(256))
    source_url = db.Column(db.Text)
    source_name = db.Column(db.String(128))
    keywords = db.Column(db.Text)
    image_url = db.Column(db.String(512))
    image_thumbnail = db.Column(db.String(512))
    published_date = db.Column(db.DateTime)

    status = db.Column(db.String(32), default='staged', index=True)
    rejection_reason = db.Column(db.Text)

    extra_data = db.Column(db.JSON)

    # Legacy CMS push tracking - kept for backward compatibility
    pushed_to_cms = db.Column(db.Boolean, default=False)
    cms_id = db.Column(db.String(128))
    pushed_at = db.Column(db.DateTime)
    pushed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Multi-version support
    selected_version_id = db.Column(db.Integer, db.ForeignKey('content_version.id', use_alter=True))

    pushed_by = db.relationship('User', backref='pushed_content')
    selected_version = db.relationship('ContentVersion', foreign_keys=[selected_version_id], post_update=True)

    def __repr__(self):
        return f'<NewsContent {self.title[:50]}>'

    def get_display_version(self):
        """Returns the selected version or falls back to legacy content fields."""
        if self.selected_version:
            return self.selected_version
        # Return self for backward compatibility (legacy content in parent)
        return self


class ContentVersion(db.Model):
    """Represents a single AI-generated version of an article."""
    id = db.Column(db.Integer, primary_key=True)
    content_id = db.Column(db.Integer, db.ForeignKey('news_content.id'), nullable=False, index=True)

    # AI Provider info
    ai_provider = db.Column(db.String(32), nullable=False)  # 'openai', 'anthropic', 'gemini'
    ai_model = db.Column(db.String(64))  # 'gpt-4', 'claude-3-opus', 'gemini-pro'
    quality_score = db.Column(db.Float)  # Optional score from n8n (0-100)

    # Content fields
    deck = db.Column(db.Text)
    teaser = db.Column(db.Text)
    content = db.Column(db.Text)
    summary = db.Column(db.Text)
    notes = db.Column(db.Text)

    # CMS push tracking (per-version)
    pushed_to_cms = db.Column(db.Boolean, default=False)
    cms_id = db.Column(db.String(128))
    pushed_at = db.Column(db.DateTime)
    pushed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    news_content = db.relationship('NewsContent', foreign_keys=[content_id], backref='versions')
    pushed_by = db.relationship('User')

    def __repr__(self):
        return f'<ContentVersion {self.ai_provider} for content {self.content_id}>'


class WorkflowRun(db.Model):
    id = db.Column(db.String(36), primary_key=True)  # UUID
    publication_id = db.Column(db.Integer, db.ForeignKey('publication.id'), nullable=False)
    triggered_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    workflow_type = db.Column(db.String(64), default='content_generation')
    status = db.Column(db.String(32), default='pending', index=True)  # pending, running, completed, failed
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    completed_at = db.Column(db.DateTime)

    publication = db.relationship('Publication', backref='workflow_runs')
    triggered_by = db.relationship('User', backref='triggered_workflows')

    def __repr__(self):
        return f'<WorkflowRun {self.id} ({self.status})>'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))