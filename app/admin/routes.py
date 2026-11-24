from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, timedelta
import json
import secrets
import requests
from sqlalchemy import func, case
from app import db
from app.models import Publication, NewsSource, User, Role, NewsletterTemplate, CandidateArticle, ResearchLog, AuthorProfile, SourceAudit
from app.admin import bp
from app.admin.forms import PublicationForm, NewsSourceForm, UserForm, NewsletterTemplateForm, AuthorProfileForm
from app.tasks import calculate_next_run, calculate_next_candidate_run
from app.sponsy import fetch_placements, fetch_ad_blocks
from app.research.scrapers import _FEED_CACHE_KEY


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
        # _feed_cache is pipeline-internal bookkeeping, not user configuration.
        visible_config = {k: v for k, v in source.config.items() if k != _FEED_CACHE_KEY}
        form.config_json.data = json.dumps(visible_config, indent=2) if visible_config else ''

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

        # Carry the hidden feed cache across an edit so saving the form doesn't
        # force every news site to re-probe its feed paths on the next run.
        cached_feed = (source.config or {}).get(_FEED_CACHE_KEY)
        if cached_feed is not None:
            config = dict(config or {})
            config[_FEED_CACHE_KEY] = cached_feed

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


@bp.route('/publications/<int:pub_id>/sources/config-assistant', methods=['POST'])
@login_required
@admin_required
def source_config_assistant(pub_id):
    """Conversational helper for authoring a source's config JSON.

    Stateless: the browser holds the transcript and posts it back each turn,
    along with the values currently typed into the form (which may not be saved
    yet), so the assistant reasons about what the editor is actually looking at.
    """
    from app.research.config_assistant import run_turn, sanitize_history

    publication = Publication.query.get_or_404(pub_id)
    payload = request.get_json(silent=True) or {}

    messages = sanitize_history(payload.get('messages'))
    if not messages:
        return jsonify({'error': 'No message to respond to'}), 400

    source_ctx = {
        'name': (payload.get('name') or '')[:256],
        'source_type': (payload.get('source_type') or '')[:64],
        'url': (payload.get('url') or '')[:512],
        'keywords': (payload.get('keywords') or '')[:2000],
        'current_config': (payload.get('current_config') or '')[:8000],
    }

    try:
        result = run_turn(messages, source_ctx, publication)
    except Exception as e:
        current_app.logger.error(f"Config assistant failed for publication {pub_id}: {e}", exc_info=True)
        return jsonify({'error': f'Assistant failed: {e}'}), 500

    return jsonify(result)


@bp.route('/publications/<int:pub_id>/sources/config-test', methods=['POST'])
@login_required
@admin_required
def source_config_test(pub_id):
    """Dry-run the discovery step of a drafted Data config without saving it."""
    from app.research.config_assistant import dry_run_discovery

    Publication.query.get_or_404(pub_id)
    payload = request.get_json(silent=True) or {}

    raw_config = payload.get('config') or ''
    try:
        config = json.loads(raw_config, strict=False) if isinstance(raw_config, str) else raw_config
    except json.JSONDecodeError as e:
        return jsonify({'ok': False, 'error': f'Invalid JSON: {e}', 'urls': [], 'log': []})

    result = dry_run_discovery(
        config,
        source_url=payload.get('url') or None,
        source_name=payload.get('name') or '(unsaved source)',
    )
    return jsonify(result)


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


# Source Audit

# Whitelist of apply-action types → required params. Mirrors the ACTION_*
# constants in app/research/audit.py. Any action not listed here is refused.
_AUDIT_APPLY_ACTIONS = {
    'update_url': {'source_id', 'url'},
    'deactivate_source': {'source_id'},
    'fix_source_type': {'source_id', 'source_type'},
    'add_source': {'name', 'source_type'},
    'delete_duplicate': {'source_id'},
}


@bp.route('/publications/<int:pub_id>/source-audits')
@login_required
@admin_required
def source_audits(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    audits = (SourceAudit.query
              .filter_by(publication_id=pub_id)
              .order_by(SourceAudit.created_at.desc())
              .all())
    return render_template('admin/source_audits.html', title='Source Audits',
                           publication=publication, audits=audits)


@bp.route('/publications/<int:pub_id>/source-audits/new', methods=['POST'])
@login_required
@admin_required
def trigger_source_audit(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    audit = SourceAudit(
        publication_id=pub_id,
        triggered_by_id=current_user.id,
        status='pending',
    )
    db.session.add(audit)
    db.session.commit()

    from app.tasks import run_source_audit
    run_source_audit.delay(audit.id)

    flash('Source audit started — this can take a couple of minutes. Refresh for results.', 'success')
    return redirect(url_for('admin.source_audit_detail', pub_id=pub_id, audit_id=audit.id))


@bp.route('/publications/<int:pub_id>/source-audits/<int:audit_id>')
@login_required
@admin_required
def source_audit_detail(pub_id, audit_id):
    publication = Publication.query.get_or_404(pub_id)
    audit = SourceAudit.query.get_or_404(audit_id)
    if audit.publication_id != pub_id:
        flash('Invalid audit for this publication', 'error')
        return redirect(url_for('admin.source_audits', pub_id=pub_id))

    findings = audit.findings or []
    severity_counts = {'critical': 0, 'warning': 0, 'info': 0}
    for f in findings:
        sev = f.get('severity', 'info')
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    return render_template('admin/source_audit_detail.html', title='Source Audit',
                           publication=publication, audit=audit, findings=findings,
                           severity_counts=severity_counts)


@bp.route('/publications/<int:pub_id>/source-audits/<int:audit_id>/apply', methods=['POST'])
@login_required
@admin_required
def apply_audit_finding(pub_id, audit_id):
    publication = Publication.query.get_or_404(pub_id)
    audit = SourceAudit.query.get_or_404(audit_id)
    if audit.publication_id != pub_id:
        flash('Invalid audit for this publication', 'error')
        return redirect(url_for('admin.source_audits', pub_id=pub_id))

    redirect_target = redirect(url_for('admin.source_audit_detail', pub_id=pub_id, audit_id=audit_id))

    finding_id = request.form.get('finding_id', type=int)
    findings = audit.findings or []
    finding = next((f for f in findings if f.get('id') == finding_id), None)
    if finding is None:
        flash('Finding not found.', 'error')
        return redirect_target

    action = finding.get('action')
    if not action or action.get('type') not in _AUDIT_APPLY_ACTIONS:
        flash('This finding has no applicable action.', 'error')
        return redirect_target
    if finding.get('applied'):
        flash('This action has already been applied.', 'info')
        return redirect_target

    action_type = action['type']
    params = action.get('params') or {}
    required = _AUDIT_APPLY_ACTIONS[action_type]
    if not required.issubset(params.keys()):
        flash('This action is missing required data and cannot be applied.', 'error')
        return redirect_target

    ok, message = _apply_source_action(pub_id, action_type, params)
    if not ok:
        flash(message, 'error')
        return redirect_target

    # Mark the finding applied so the button disappears. Reassign the list so
    # SQLAlchemy detects the JSON change.
    for f in findings:
        if f.get('id') == finding_id:
            f['applied'] = True
    audit.findings = list(findings)
    db.session.commit()

    flash(message, 'success')
    return redirect_target


def _apply_source_action(pub_id, action_type, params):
    """Execute one whitelisted audit fix. Returns (ok, message).

    Every source-targeted action re-verifies the source belongs to pub_id, so a
    stale or tampered finding cannot touch another publication's data.
    """
    def _owned_source(source_id):
        s = NewsSource.query.get(source_id)
        if s is None or s.publication_id != pub_id:
            return None
        return s

    if action_type == 'add_source':
        stype = params.get('source_type')
        source = NewsSource(
            publication_id=pub_id,
            name=params.get('name'),
            source_type=stype,
            url=params.get('url'),
            keywords=params.get('keywords'),
            is_active=True,
        )
        db.session.add(source)
        return True, f'Added source "{source.name}".'

    source = _owned_source(params.get('source_id'))
    if source is None:
        return False, 'The target source no longer exists.'

    if action_type == 'update_url':
        source.url = params.get('url')
        return True, f'Updated URL for "{source.name}".'
    if action_type == 'deactivate_source':
        source.is_active = False
        return True, f'Deactivated "{source.name}".'
    if action_type == 'fix_source_type':
        source.source_type = params.get('source_type')
        return True, f'Set source type for "{source.name}".'
    if action_type == 'delete_duplicate':
        name = source.name
        db.session.delete(source)
        return True, f'Deleted duplicate source "{name}".'

    return False, 'Unknown action.'


# Author Profile CRUD

@bp.route('/publications/<int:pub_id>/authors')
@login_required
@admin_required
def author_profiles(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    profiles = AuthorProfile.query.filter_by(publication_id=pub_id).order_by(AuthorProfile.name).all()
    return render_template('admin/author_profiles.html', title='Author Profiles',
                           publication=publication, profiles=profiles)


@bp.route('/publications/<int:pub_id>/authors/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_author_profile(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    form = AuthorProfileForm()

    if form.validate_on_submit():
        # Build sample_articles list from text and URLs
        samples = []
        if form.sample_articles_text.data and form.sample_articles_text.data.strip():
            for chunk in form.sample_articles_text.data.split('---'):
                chunk = chunk.strip()
                if chunk:
                    samples.append(chunk)
        if form.sample_article_urls.data and form.sample_article_urls.data.strip():
            for line in form.sample_article_urls.data.strip().splitlines():
                url = line.strip()
                if url:
                    samples.append(url)

        # If setting as default, clear other defaults
        if form.is_default.data:
            AuthorProfile.query.filter_by(publication_id=pub_id, is_default=True).update({'is_default': False})

        profile = AuthorProfile(
            publication_id=pub_id,
            name=form.name.data,
            sample_articles=samples if samples else None,
            is_default=form.is_default.data,
            is_active=form.is_active.data,
        )
        db.session.add(profile)
        db.session.commit()

        # Trigger style guide generation if samples were provided
        if samples:
            from app.tasks import generate_author_style_guide
            generate_author_style_guide.delay(profile.id)
            flash(f'Author profile "{profile.name}" created. Style guide is being generated...', 'success')
        else:
            flash(f'Author profile "{profile.name}" created. Add sample articles to generate a style guide.', 'success')

        return redirect(url_for('admin.author_profiles', pub_id=pub_id))

    return render_template('admin/author_profile_form.html', title='New Author Profile',
                           form=form, publication=publication)


@bp.route('/publications/<int:pub_id>/authors/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_author_profile(pub_id, id):
    publication = Publication.query.get_or_404(pub_id)
    profile = AuthorProfile.query.get_or_404(id)
    if profile.publication_id != pub_id:
        flash('Invalid author for this publication', 'error')
        return redirect(url_for('admin.author_profiles', pub_id=pub_id))

    form = AuthorProfileForm(obj=profile)

    if request.method == 'GET':
        # Pre-populate sample fields
        samples = profile.sample_articles or []
        urls = [s for s in samples if isinstance(s, str) and s.startswith('http')]
        texts = [s for s in samples if isinstance(s, str) and not s.startswith('http')]
        form.sample_article_urls.data = '\n'.join(urls) if urls else ''
        form.sample_articles_text.data = '\n---\n'.join(texts) if texts else ''

    if form.validate_on_submit():
        samples = []
        if form.sample_articles_text.data and form.sample_articles_text.data.strip():
            for chunk in form.sample_articles_text.data.split('---'):
                chunk = chunk.strip()
                if chunk:
                    samples.append(chunk)
        if form.sample_article_urls.data and form.sample_article_urls.data.strip():
            for line in form.sample_article_urls.data.strip().splitlines():
                url = line.strip()
                if url:
                    samples.append(url)

        if form.is_default.data and not profile.is_default:
            AuthorProfile.query.filter_by(publication_id=pub_id, is_default=True).update({'is_default': False})

        profile.name = form.name.data
        profile.sample_articles = samples if samples else None
        profile.is_default = form.is_default.data
        profile.is_active = form.is_active.data
        db.session.commit()
        flash(f'Author profile "{profile.name}" updated.', 'success')
        return redirect(url_for('admin.author_profiles', pub_id=pub_id))

    return render_template('admin/author_profile_form.html', title='Edit Author Profile',
                           form=form, publication=publication, profile=profile)


@bp.route('/publications/<int:pub_id>/authors/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_author_profile(pub_id, id):
    profile = AuthorProfile.query.get_or_404(id)
    if profile.publication_id != pub_id:
        flash('Invalid author for this publication', 'error')
        return redirect(url_for('admin.author_profiles', pub_id=pub_id))

    db.session.delete(profile)
    db.session.commit()
    flash(f'Author profile deleted.', 'success')
    return redirect(url_for('admin.author_profiles', pub_id=pub_id))


@bp.route('/publications/<int:pub_id>/authors/<int:id>/generate-guide', methods=['POST'])
@login_required
@admin_required
def generate_author_guide(pub_id, id):
    profile = AuthorProfile.query.get_or_404(id)
    if profile.publication_id != pub_id:
        return jsonify({'error': 'Invalid author for this publication'}), 400

    if not profile.sample_articles:
        return jsonify({'error': 'No sample articles to analyze'}), 400

    from app.tasks import generate_author_style_guide
    generate_author_style_guide.delay(profile.id)
    return jsonify({'success': True})


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

    from app.tasks import start_research
    _, created = start_research(publication.id, trigger='admin')

    if created:
        flash(f'Research triggered for {publication.name}!', 'success')
    else:
        flash(f'Research is already running for {publication.name}.', 'info')
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