from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, timedelta
import json
import secrets
import requests
from sqlalchemy import func, case
from app import db
from app.models import Publication, NewsSource, User, Role, NewsletterTemplate, CandidateArticle, ResearchLog
from app.admin import bp
from app.admin.forms import PublicationForm, NewsSourceForm, UserForm, NewsletterTemplateForm
from app.tasks import calculate_next_run, calculate_next_candidate_run
from app.sponsy import fetch_placements, fetch_ad_blocks


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
            publication_domain=form.publication_domain.data,
            industry_description=form.industry_description.data,
            reader_personas=form.reader_personas.data,
            reader_pain_points=form.reader_pain_points.data,
            access_api_key=form.access_api_key.data,
            cms_url=form.cms_url.data,
            cms_api_key=form.cms_api_key.data,
            ghost_url=form.ghost_url.data or None,
            ghost_admin_api_key=form.ghost_admin_api_key.data or None,
            ghost_newsletter_slug=form.ghost_newsletter_slug.data or None,
            sponsy_api_key=form.sponsy_api_key.data or None,
            sponsy_publication_id=form.sponsy_publication_id.data or None,
            is_active=form.is_active.data,
            # Notifications
            notification_emails=form.notification_emails.data or None,
            # Research fields
            require_candidate_review=form.require_candidate_review.data,
            # Scheduling fields
            schedule_enabled=form.schedule_enabled.data,
            schedule_frequency=form.schedule_frequency.data or None,
            schedule_time=form.schedule_time.data or None,
            schedule_day_of_week=int(form.schedule_day_of_week.data) if form.schedule_day_of_week.data else None,
            # Candidate content generation scheduling
            candidate_schedule_enabled=form.candidate_schedule_enabled.data,
            candidate_schedule_frequency=form.candidate_schedule_frequency.data or None,
            candidate_schedule_time=form.candidate_schedule_time.data or None,
            candidate_schedule_day_of_week=int(form.candidate_schedule_day_of_week.data) if form.candidate_schedule_day_of_week.data else None
        )

        # Calculate next scheduled run if scheduling is enabled
        if publication.schedule_enabled and publication.schedule_frequency and publication.schedule_time:
            publication.next_scheduled_run = calculate_next_run(publication)

        if publication.candidate_schedule_enabled and publication.candidate_schedule_frequency and publication.candidate_schedule_time:
            publication.next_candidate_schedule_run = calculate_next_candidate_run(publication)

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

    # Pre-populate day_of_week fields as strings for SelectField
    if request.method == 'GET':
        if publication.schedule_day_of_week is not None:
            form.schedule_day_of_week.data = str(publication.schedule_day_of_week)
        if publication.candidate_schedule_day_of_week is not None:
            form.candidate_schedule_day_of_week.data = str(publication.candidate_schedule_day_of_week)

    if form.validate_on_submit():
        publication.name = form.name.data
        publication.publication_domain = form.publication_domain.data
        publication.industry_description = form.industry_description.data
        publication.reader_personas = form.reader_personas.data
        publication.reader_pain_points = form.reader_pain_points.data
        publication.access_api_key = form.access_api_key.data
        publication.cms_url = form.cms_url.data
        publication.cms_api_key = form.cms_api_key.data
        publication.ghost_url = form.ghost_url.data or None
        publication.ghost_admin_api_key = form.ghost_admin_api_key.data or None
        publication.ghost_newsletter_slug = form.ghost_newsletter_slug.data or None
        publication.sponsy_api_key = form.sponsy_api_key.data or None
        publication.sponsy_publication_id = form.sponsy_publication_id.data or None
        publication.is_active = form.is_active.data

        # Notifications
        publication.notification_emails = form.notification_emails.data or None

        # Research fields
        publication.require_candidate_review = form.require_candidate_review.data

        # Scheduling fields
        publication.schedule_enabled = form.schedule_enabled.data
        publication.schedule_frequency = form.schedule_frequency.data or None
        publication.schedule_time = form.schedule_time.data or None
        publication.schedule_day_of_week = int(form.schedule_day_of_week.data) if form.schedule_day_of_week.data else None

        # Candidate content generation scheduling
        publication.candidate_schedule_enabled = form.candidate_schedule_enabled.data
        publication.candidate_schedule_frequency = form.candidate_schedule_frequency.data or None
        publication.candidate_schedule_time = form.candidate_schedule_time.data or None
        publication.candidate_schedule_day_of_week = int(form.candidate_schedule_day_of_week.data) if form.candidate_schedule_day_of_week.data else None

        # Calculate next scheduled run if scheduling is enabled
        if publication.schedule_enabled and publication.schedule_frequency and publication.schedule_time:
            publication.next_scheduled_run = calculate_next_run(publication)
        else:
            publication.next_scheduled_run = None

        if publication.candidate_schedule_enabled and publication.candidate_schedule_frequency and publication.candidate_schedule_time:
            publication.next_candidate_schedule_run = calculate_next_candidate_run(publication)
        else:
            publication.next_candidate_schedule_run = None

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

    # Source performance stats via grouped conditional aggregation
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    stats_rows = db.session.query(
        CandidateArticle.news_source_id,
        func.count(CandidateArticle.id).label('total_candidates'),
        func.count(case(
            (CandidateArticle.discovered_at >= thirty_days_ago, CandidateArticle.id)
        )).label('recent_candidates'),
        func.count(case(
            (CandidateArticle.status.in_(['selected', 'processed']), CandidateArticle.id)
        )).label('selected_count'),
        func.count(case(
            (CandidateArticle.status == 'rejected', CandidateArticle.id)
        )).label('rejected_count'),
        func.count(case(
            (CandidateArticle.news_content_id.isnot(None), CandidateArticle.id)
        )).label('articles_created'),
        func.avg(CandidateArticle.relevance_score).label('avg_relevance'),
    ).filter(
        CandidateArticle.publication_id == pub_id,
        CandidateArticle.news_source_id.isnot(None)
    ).group_by(CandidateArticle.news_source_id).all()

    source_stats = {}
    for row in stats_rows:
        total = row.total_candidates
        source_stats[row.news_source_id] = {
            'total_candidates': total,
            'recent_candidates': row.recent_candidates,
            'selected_pct': round(row.selected_count / total * 100) if total else 0,
            'rejected_count': row.rejected_count,
            'rejected_pct': round(row.rejected_count / total * 100) if total else 0,
            'articles_created': row.articles_created,
            'avg_relevance': round(row.avg_relevance, 1) if row.avg_relevance else None,
        }

    return render_template('admin/news_sources.html', title='News Sources',
                           publication=publication, sources=sources, source_stats=source_stats)


@bp.route('/publications/<int:pub_id>/sources/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_news_source(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    form = NewsSourceForm()

    if form.validate_on_submit():
        config = None
        if form.config_json.data and form.config_json.data.strip():
            try:
                config = json.loads(form.config_json.data, strict=False)
            except json.JSONDecodeError as e:
                flash(f'Invalid JSON in configuration: {e}', 'error')
                return render_template('admin/news_source_form.html', title='New News Source', form=form, publication=publication)

        source = NewsSource(
            publication_id=pub_id,
            name=form.name.data,
            source_type=form.source_type.data,
            url=form.url.data,
            keywords=form.keywords.data,
            config=config,
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

    if request.method == 'GET' and source.config:
        form.config_json.data = json.dumps(source.config, indent=2)

    if form.validate_on_submit():
        config = source.config
        if form.config_json.data and form.config_json.data.strip():
            try:
                config = json.loads(form.config_json.data, strict=False)
            except json.JSONDecodeError as e:
                flash(f'Invalid JSON in configuration: {e}', 'error')
                return render_template('admin/news_source_form.html', title='Edit News Source', form=form, publication=publication, source=source)
        elif not form.config_json.data or not form.config_json.data.strip():
            config = None

        source.name = form.name.data
        source.source_type = form.source_type.data
        source.url = form.url.data
        source.keywords = form.keywords.data
        source.config = config
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


@bp.route('/publications/<int:pub_id>/sources/<int:id>/retriage', methods=['POST'])
@login_required
@admin_required
def retriage_source(pub_id, id):
    source = NewsSource.query.get_or_404(id)
    if source.publication_id != pub_id:
        flash('Invalid source for this publication', 'error')
        return redirect(url_for('admin.news_sources', pub_id=pub_id))

    from app.tasks import retriage_source_candidates
    retriage_source_candidates.delay(source.id)

    flash(f'Re-triage triggered for rejected candidates from {source.name}!', 'success')
    return redirect(url_for('admin.news_sources', pub_id=pub_id))


@bp.route('/publications/<int:pub_id>/research-logs')
@login_required
@admin_required
def research_logs(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    page = request.args.get('page', 1, type=int)
    source_filter = request.args.get('source_id', type=int)
    level_filter = request.args.get('level')
    phase_filter = request.args.get('phase')

    query = ResearchLog.query.filter_by(publication_id=pub_id)
    if source_filter:
        query = query.filter_by(news_source_id=source_filter)
    if level_filter:
        query = query.filter_by(level=level_filter)
    if phase_filter:
        query = query.filter_by(phase=phase_filter)

    logs = query.order_by(ResearchLog.created_at.desc()).paginate(page=page, per_page=50, error_out=False)
    sources = NewsSource.query.filter_by(publication_id=pub_id).order_by(NewsSource.name).all()

    return render_template('admin/research_logs.html', title='Research Logs',
                           publication=publication, logs=logs, sources=sources,
                           source_filter=source_filter, level_filter=level_filter,
                           phase_filter=phase_filter)


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
        # Fire-and-forget: use a very short timeout just to send the request
        requests.get(
            workflow_url,
            params={'publication_id': publication.id},
            timeout=0.5
        )
    except requests.exceptions.Timeout:
        # Expected - we're not waiting for a response
        pass
    except requests.exceptions.RequestException as e:
        flash(f'Failed to trigger workflow: {str(e)}', 'error')
        return redirect(url_for('admin.publications'))

    flash(f'Content generation workflow triggered for {publication.name}!', 'success')
    return redirect(url_for('admin.publications'))


@bp.route('/publications/<int:id>/trigger-research', methods=['POST'])
@login_required
@admin_required
def trigger_research(id):
    publication = Publication.query.get_or_404(id)

    from app.tasks import research_publication_sources
    research_publication_sources.delay(publication.id)

    flash(f'Research triggered for {publication.name}!', 'success')
    return redirect(url_for('admin.publications'))


# Newsletter Template CRUD

@bp.route('/publications/<int:pub_id>/newsletter-templates')
@login_required
@admin_required
def newsletter_templates(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    templates = NewsletterTemplate.query.filter_by(publication_id=pub_id).all()
    return render_template('admin/newsletter_templates.html',
                           title='Newsletter Templates',
                           publication=publication,
                           templates=templates)


@bp.route('/publications/<int:pub_id>/newsletter-templates/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_newsletter_template(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    form = NewsletterTemplateForm()

    # Populate Sponsy placement and ad block choices
    if publication.sponsy_api_key and publication.sponsy_publication_id:
        placements = fetch_placements(publication.sponsy_api_key, publication.sponsy_publication_id)
        placement_choices = [('', '-- None --')] + placements
        ad_blocks = fetch_ad_blocks(publication.sponsy_api_key, publication.sponsy_publication_id)
        block_choices = [('', '-- None --')] + ad_blocks
    else:
        placement_choices = [('', '-- Sponsy not configured --')]
        block_choices = [('', '-- Sponsy not configured --')]
    form.sponsy_top_placement_id.choices = placement_choices
    form.sponsy_mid_placement_id.choices = placement_choices
    form.sponsy_top_ad_block_id.choices = block_choices
    form.sponsy_mid_ad_block_id.choices = block_choices

    if form.validate_on_submit():
        template = NewsletterTemplate(
            publication_id=pub_id,
            name=form.name.data,
            header_html=form.header_html.data,
            footer_html=form.footer_html.data,
            primary_color=form.primary_color.data,
            secondary_color=form.secondary_color.data,
            include_intro=form.include_intro.data,
            max_articles=form.max_articles.data,
            sponsy_top_placement_id=form.sponsy_top_placement_id.data or None,
            sponsy_mid_placement_id=form.sponsy_mid_placement_id.data or None,
            sponsy_top_ad_block_id=form.sponsy_top_ad_block_id.data or None,
            sponsy_mid_ad_block_id=form.sponsy_mid_ad_block_id.data or None,
            sponsy_mid_position=form.sponsy_mid_position.data or 3,
            is_active=form.is_active.data,
        )
        db.session.add(template)
        db.session.commit()
        flash('Newsletter template created.', 'success')
        return redirect(url_for('admin.newsletter_templates', pub_id=pub_id))

    return render_template('admin/newsletter_template_form.html',
                           title='New Newsletter Template',
                           form=form,
                           publication=publication)


@bp.route('/newsletter-templates/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_newsletter_template(id):
    template = NewsletterTemplate.query.get_or_404(id)
    publication = template.publication
    form = NewsletterTemplateForm(obj=template)

    # Populate Sponsy placement and ad block choices
    if publication.sponsy_api_key and publication.sponsy_publication_id:
        placements = fetch_placements(publication.sponsy_api_key, publication.sponsy_publication_id)
        placement_choices = [('', '-- None --')] + placements
        ad_blocks = fetch_ad_blocks(publication.sponsy_api_key, publication.sponsy_publication_id)
        block_choices = [('', '-- None --')] + ad_blocks
    else:
        placement_choices = [('', '-- Sponsy not configured --')]
        block_choices = [('', '-- Sponsy not configured --')]
    form.sponsy_top_placement_id.choices = placement_choices
    form.sponsy_mid_placement_id.choices = placement_choices
    form.sponsy_top_ad_block_id.choices = block_choices
    form.sponsy_mid_ad_block_id.choices = block_choices

    if form.validate_on_submit():
        template.name = form.name.data
        template.header_html = form.header_html.data
        template.footer_html = form.footer_html.data
        template.primary_color = form.primary_color.data
        template.secondary_color = form.secondary_color.data
        template.include_intro = form.include_intro.data
        template.max_articles = form.max_articles.data
        template.sponsy_top_placement_id = form.sponsy_top_placement_id.data or None
        template.sponsy_mid_placement_id = form.sponsy_mid_placement_id.data or None
        template.sponsy_top_ad_block_id = form.sponsy_top_ad_block_id.data or None
        template.sponsy_mid_ad_block_id = form.sponsy_mid_ad_block_id.data or None
        template.sponsy_mid_position = form.sponsy_mid_position.data or 3
        template.is_active = form.is_active.data
        db.session.commit()
        flash('Newsletter template updated.', 'success')
        return redirect(url_for('admin.newsletter_templates', pub_id=publication.id))

    return render_template('admin/newsletter_template_form.html',
                           title='Edit Newsletter Template',
                           form=form,
                           publication=publication,
                           template=template)


@bp.route('/newsletter-templates/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_newsletter_template(id):
    template = NewsletterTemplate.query.get_or_404(id)
    pub_id = template.publication_id
    db.session.delete(template)
    db.session.commit()
    flash('Newsletter template deleted.', 'success')
    return redirect(url_for('admin.newsletter_templates', pub_id=pub_id))