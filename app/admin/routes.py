from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps
import secrets
import requests
from app import db
from app.models import Publication, NewsSource, User, Role
from app.admin import bp
from app.admin.forms import PublicationForm, NewsSourceForm, UserForm


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_role('admin'):
            flash('You need administrator privileges to access this page.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def generate_api_key():
    """Generate a secure random API key"""
    return secrets.token_urlsafe(32)


@bp.route('/')
@login_required
@admin_required
def index():
    return render_template('admin/index.html', title='Admin Dashboard')


@bp.route('/publications')
@login_required
@admin_required
def publications():
    all_publications = Publication.query.all()
    return render_template('admin/publications.html', title='Publications', publications=all_publications)


@bp.route('/publications/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_publication():
    form = PublicationForm()
    if form.validate_on_submit():
        publication = Publication(
            name=form.name.data,
            slug=form.slug.data,
            industry_description=form.industry_description.data,
            reader_personas=form.reader_personas.data,
            reader_pain_points=form.reader_pain_points.data,
            access_api_key=form.access_api_key.data,
            cms_url=form.cms_url.data,
            cms_api_key=form.cms_api_key.data,
            is_active=form.is_active.data
        )
        db.session.add(publication)
        db.session.commit()
        flash('Publication created successfully!', 'success')
        return redirect(url_for('admin.publications'))
    return render_template('admin/publication_form.html', title='New Publication', form=form)


@bp.route('/publications/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_publication(id):
    publication = Publication.query.get_or_404(id)
    form = PublicationForm(obj=publication)

    if form.validate_on_submit():
        publication.name = form.name.data
        publication.slug = form.slug.data
        publication.industry_description = form.industry_description.data
        publication.reader_personas = form.reader_personas.data
        publication.reader_pain_points = form.reader_pain_points.data
        publication.access_api_key = form.access_api_key.data
        publication.cms_url = form.cms_url.data
        publication.cms_api_key = form.cms_api_key.data
        publication.is_active = form.is_active.data
        db.session.commit()
        flash('Publication updated successfully!', 'success')
        return redirect(url_for('admin.publications'))

    return render_template('admin/publication_form.html', title='Edit Publication', form=form, publication=publication)


@bp.route('/publications/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_publication(id):
    publication = Publication.query.get_or_404(id)
    db.session.delete(publication)
    db.session.commit()
    flash('Publication deleted successfully!', 'success')
    return redirect(url_for('admin.publications'))


@bp.route('/publications/<int:id>/generate-access-api-key', methods=['POST'])
@login_required
@admin_required
def generate_publication_access_api_key(id):
    publication = Publication.query.get_or_404(id)
    new_api_key = generate_api_key()
    publication.access_api_key = new_api_key
    db.session.commit()
    return jsonify({'success': True, 'api_key': new_api_key})


@bp.route('/publications/<int:pub_id>/sources')
@login_required
@admin_required
def news_sources(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    sources = NewsSource.query.filter_by(publication_id=pub_id).all()
    return render_template('admin/news_sources.html', title='News Sources', publication=publication, sources=sources)


@bp.route('/publications/<int:pub_id>/sources/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_news_source(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    form = NewsSourceForm()

    if form.validate_on_submit():
        source = NewsSource(
            publication_id=pub_id,
            name=form.name.data,
            source_type=form.source_type.data,
            url=form.url.data,
            keywords=form.keywords.data,
            is_active=form.is_active.data
        )
        db.session.add(source)
        db.session.commit()
        flash('News source created successfully!', 'success')
        return redirect(url_for('admin.news_sources', pub_id=pub_id))

    return render_template('admin/news_source_form.html', title='New News Source', form=form, publication=publication)


@bp.route('/publications/<int:pub_id>/sources/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_news_source(pub_id, id):
    publication = Publication.query.get_or_404(pub_id)
    source = NewsSource.query.get_or_404(id)

    if source.publication_id != pub_id:
        flash('Invalid source for this publication', 'error')
        return redirect(url_for('admin.news_sources', pub_id=pub_id))

    form = NewsSourceForm(obj=source)

    if form.validate_on_submit():
        source.name = form.name.data
        source.source_type = form.source_type.data
        source.url = form.url.data
        source.keywords = form.keywords.data
        source.is_active = form.is_active.data
        db.session.commit()
        flash('News source updated successfully!', 'success')
        return redirect(url_for('admin.news_sources', pub_id=pub_id))

    return render_template('admin/news_source_form.html', title='Edit News Source', form=form, publication=publication, source=source)


@bp.route('/publications/<int:pub_id>/sources/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_news_source(pub_id, id):
    source = NewsSource.query.get_or_404(id)
    if source.publication_id != pub_id:
        flash('Invalid source for this publication', 'error')
        return redirect(url_for('admin.news_sources', pub_id=pub_id))

    db.session.delete(source)
    db.session.commit()
    flash('News source deleted successfully!', 'success')
    return redirect(url_for('admin.news_sources', pub_id=pub_id))


@bp.route('/users')
@login_required
@admin_required
def users():
    all_users = User.query.all()
    return render_template('admin/users.html', title='Users', users=all_users)


@bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_user():
    form = UserForm()

    # Populate choices
    form.roles.choices = [(r.id, r.name) for r in Role.query.all()]
    form.publications.choices = [(p.id, p.name) for p in Publication.query.all()]

    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            is_active=form.is_active.data
        )
        user.set_password(form.password.data)

        # Add roles
        for role_id in form.roles.data:
            role = Role.query.get(role_id)
            if role:
                user.roles.append(role)

        # Add publications
        for pub_id in form.publications.data:
            pub = Publication.query.get(pub_id)
            if pub:
                user.publications.append(pub)

        db.session.add(user)
        db.session.commit()
        flash('User created successfully!', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', title='New User', form=form)


@bp.route('/users/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(id):
    user = User.query.get_or_404(id)
    form = UserForm(original_username=user.username, original_email=user.email, obj=user)

    # Populate choices
    form.roles.choices = [(r.id, r.name) for r in Role.query.all()]
    form.publications.choices = [(p.id, p.name) for p in Publication.query.all()]

    if request.method == 'GET':
        form.roles.data = [r.id for r in user.roles]
        form.publications.data = [p.id for p in user.publications]

    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.is_active = form.is_active.data

        # Update password only if provided
        if form.password.data:
            user.set_password(form.password.data)

        # Update roles
        user.roles = []
        for role_id in form.roles.data:
            role = Role.query.get(role_id)
            if role:
                user.roles.append(role)

        # Update publications
        user.publications = []
        for pub_id in form.publications.data:
            pub = Publication.query.get(pub_id)
            if pub:
                user.publications.append(pub)

        db.session.commit()
        flash('User updated successfully!', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', title='Edit User', form=form, user=user)


@bp.route('/users/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(id):
    user = User.query.get_or_404(id)

    # Prevent deleting yourself
    if user.id == current_user.id:
        flash('You cannot delete your own account!', 'error')
        return redirect(url_for('admin.users'))

    db.session.delete(user)
    db.session.commit()
    flash('User deleted successfully!', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/publications/<int:id>/trigger-content-workflow', methods=['POST'])
@login_required
@admin_required
def trigger_content_workflow(id):
    publication = Publication.query.get_or_404(id)

    workflow_url = current_app.config.get('N8N_CONTENT_WORKFLOW_URL')
    if not workflow_url:
        flash('Content workflow URL is not configured. Set N8N_CONTENT_WORKFLOW_URL environment variable.', 'error')
        return redirect(url_for('admin.publications'))

    try:
        response = requests.get(
            workflow_url,
            json={'publication_id': publication.id},
            timeout=30
        )
        response.raise_for_status()
        flash(f'Content generation workflow triggered for {publication.name}!', 'success')
    except requests.exceptions.Timeout:
        flash('Workflow triggered but response timed out. The workflow may still be running.', 'warning')
    except requests.exceptions.RequestException as e:
        flash(f'Failed to trigger workflow: {str(e)}', 'error')

    return redirect(url_for('admin.publications'))