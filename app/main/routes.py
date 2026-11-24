from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from datetime import datetime
import uuid
from app import db
from app.models import NewsContent, Publication, WorkflowRun, ContentVersion, VersionAudit, PatchedVersion, VersionRevision, CandidateArticle, WeeklyBriefing, AuthorProfile, NewsSource, ContentRecipe, StaticSource
from app.main import bp
from app.publication_context import resolve_publication_id
import requests


@bp.route('/')
@login_required
def index():
    return redirect(url_for('main.dashboard'))


CONTENT_SORT_COLUMNS = {
    'title': NewsContent.title,
    'source': NewsContent.source_name,
    'status': NewsContent.status,
    'date': NewsContent.created_at,
}


@bp.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'staged')
    sort = request.args.get('sort', 'date')
    direction = request.args.get('direction', 'desc' if sort == 'date' else 'asc')
    if sort not in CONTENT_SORT_COLUMNS:
        sort = 'date'
    if direction not in ('asc', 'desc'):
        direction = 'desc'

    # Get available publications for the user
    if current_user.has_role('admin'):
        publications = Publication.query.filter_by(is_active=True).order_by(Publication.id).all()
    else:
        publications = [p for p in current_user.publications if p.is_active]

    publication_id = resolve_publication_id(publications)
    current_publication = Publication.query.get(publication_id) if publication_id else None

    # Get latest weekly briefing and author profiles for the current publication
    briefing = None
    author_profiles = []
    recipe_candidates_list = []
    recipes = []
    static_sources = []
    if publication_id:
        briefing = WeeklyBriefing.query.filter_by(
            publication_id=publication_id
        ).order_by(WeeklyBriefing.created_at.desc()).first()
        author_profiles = AuthorProfile.query.filter_by(
            publication_id=publication_id, is_active=True
        ).order_by(AuthorProfile.is_default.desc(), AuthorProfile.name).all()
        # Candidates available to combine into a recipe, and saved recipes.
        # Include 'processed' so already-used candidates can be reused; only
        # 'rejected' candidates are hidden.
        recipe_candidates_list = CandidateArticle.query.filter(
            CandidateArticle.publication_id == publication_id,
            CandidateArticle.status != 'rejected'
        ).order_by(CandidateArticle.relevance_score.desc()).all()
        recipes = ContentRecipe.query.filter_by(
            publication_id=publication_id
        ).order_by(ContentRecipe.created_at.desc()).all()
        static_sources = StaticSource.query.filter_by(
            publication_id=publication_id
        ).order_by(StaticSource.created_at.desc()).all()

    query = NewsContent.query

    # Filter by selected publication
    if publication_id:
        query = query.filter_by(publication_id=publication_id)
    elif not current_user.has_role('admin') and current_user.publications:
        pub_ids = current_user.get_publication_ids()
        query = query.filter(NewsContent.publication_id.in_(pub_ids))

    if status != 'all':
        query = query.filter_by(status=status)

    sort_col = CONTENT_SORT_COLUMNS[sort]
    sort_expr = sort_col.desc() if direction == 'desc' else sort_col.asc()
    content = query.order_by(sort_expr, NewsContent.id.desc()).paginate(
        page=page, per_page=current_app.config['ITEMS_PER_PAGE'], error_out=False
    )

    return render_template('main/dashboard.html', title='Dashboard', content=content, status=status,
                           sort=sort, direction=direction,
                           publications=publications, current_publication=current_publication,
                           briefing=briefing, author_profiles=author_profiles,
                           recipe_candidates_list=recipe_candidates_list, recipes=recipes,
                           static_sources=static_sources)


@bp.route('/dashboard/regenerate-briefing', methods=['POST'])
@login_required
def regenerate_briefing():
    publication_id = request.json.get('publication_id')
    if not publication_id:
        return jsonify({'error': 'publication_id required'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(publication_id):
        return jsonify({'error': 'Access denied'}), 403

    from app.tasks import generate_weekly_briefings
    generate_weekly_briefings.delay(publication_id)
    return jsonify({'success': True})


@bp.route('/publication/<int:pub_id>/author-profiles')
@login_required
def get_author_profiles(pub_id):
    """Return active author profiles for a publication (JSON, used by author selection modal)."""
    profiles = AuthorProfile.query.filter_by(
        publication_id=pub_id, is_active=True
    ).order_by(AuthorProfile.is_default.desc(), AuthorProfile.name).all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'is_default': p.is_default,
        'style_guide_preview': (p.style_guide[:150] + '...') if p.style_guide and len(p.style_guide) > 150 else (p.style_guide or ''),
        'has_style_guide': bool(p.style_guide),
    } for p in profiles])


@bp.route('/content/<int:id>')
@login_required
def view_content(id):
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        flash('Access denied', 'error')
        return redirect(url_for('main.dashboard'))

    return render_template('main/content_detail.html', title=content.title, content=content)


@bp.route('/content/<int:id>/push', methods=['POST'])
@login_required
def push_to_cms(id):
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if content.pushed_to_cms:
        return jsonify({'error': 'Content already pushed to CMS'}), 400

    publication = content.publication
    if not publication.cms_url or not publication.cms_api_key:
        return jsonify({'error': 'CMS configuration not set for this publication'}), 400

    try:
        # Build props with available fields
        # CMS API props: title, type, body, deck, excerpt, byline, creditOrSource, notes, slug
        props = {
            'title': content.title,
            'type': 'NEWS',
            'body': content.content
        }

        # Add optional fields if they exist
        if content.deck:
            props['deck'] = content.deck
        if content.teaser:
            props['excerpt'] = content.teaser  # CMS calls it 'excerpt', not 'teaser'

        payload = {
            'command': {
                'input': [
                    {
                        'form': 'POST',
                        'props': props
                    }
                ]
            },
            'view': 'main'
        }

        # Add Bearer prefix if not already present
        api_key = publication.cms_api_key
        if not api_key.lower().startswith('bearer '):
            api_key = f'Bearer {api_key}'

        headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json',
            'x-namespace': 'watt/default'
        }

        response = requests.post(publication.cms_url, json=payload, headers=headers, timeout=30)

        # Check for errors and return the actual API error message
        if not response.ok:
            try:
                error_detail = response.json()
            except Exception:
                error_detail = response.text
            # Include detail in error message so it shows in UI alert
            error_msg = f'CMS API error ({response.status_code}): {error_detail}'
            current_app.logger.error(f'CMS push failed: {error_msg}')
            current_app.logger.error(f'Payload sent: {payload}')
            return jsonify({
                'error': error_msg,
                'detail': error_detail,
                'payload_sent': payload
            }), 500  # Return 500 so our UI handles it

        cms_response = response.json()
        content.pushed_to_cms = True
        content.cms_id = cms_response.get('id')
        content.pushed_at = datetime.utcnow()
        content.pushed_by_id = current_user.id
        content.status = 'published'

        db.session.commit()

        return jsonify({'success': True, 'message': 'Content pushed to CMS successfully', 'cms_id': content.cms_id})

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to push to CMS: {str(e)}', 'type': 'request_exception'}), 500
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@bp.route('/content/<int:id>/version/<int:version_id>/push', methods=['POST'])
@login_required
def push_version_to_cms(id, version_id):
    """Push a specific version of content to CMS."""
    content = NewsContent.query.get_or_404(id)
    version = ContentVersion.query.get_or_404(version_id)

    # Verify version belongs to this content
    if version.content_id != content.id:
        return jsonify({'error': 'Version does not belong to this content'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if version.pushed_to_cms:
        return jsonify({'error': 'This version has already been pushed to CMS'}), 400

    publication = content.publication
    if not publication.cms_url or not publication.cms_api_key:
        return jsonify({'error': 'CMS configuration not set for this publication'}), 400

    try:
        import markdown as md

        # Convert Markdown body to HTML so basic formatting (subheads, bold,
        # italic, paragraphs/line breaks) is preserved in the CMS.
        body_html = md.markdown(version.content, extensions=['nl2br', 'fenced_code', 'tables'])

        # Build props with available fields from version
        # CMS API props: title, type, body, deck, excerpt, byline, creditOrSource, notes, slug
        props = {
            'title': content.title,
            'type': 'NEWS',
            'body': body_html
        }

        # Add optional fields from version if they exist
        if version.deck:
            props['deck'] = version.deck
        if version.teaser:
            props['excerpt'] = version.teaser  # CMS calls it 'excerpt', not 'teaser'

        payload = {
            'command': {
                'input': [
                    {
                        'form': 'POST',
                        'props': props
                    }
                ]
            },
            'view': 'main'
        }

        api_key = publication.cms_api_key
        if not api_key.lower().startswith('bearer '):
            api_key = f'Bearer {api_key}'

        headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json',
            'x-namespace': 'watt/default'
        }

        response = requests.post(publication.cms_url, json=payload, headers=headers, timeout=30)

        # Check for errors and return the actual API error message
        if not response.ok:
            try:
                error_detail = response.json()
            except Exception:
                error_detail = response.text
            # Include detail in error message so it shows in UI alert
            error_msg = f'CMS API error ({response.status_code}): {error_detail}'
            current_app.logger.error(f'CMS push failed: {error_msg}')
            current_app.logger.error(f'Payload sent: {payload}')
            return jsonify({
                'error': error_msg,
                'detail': error_detail,
                'payload_sent': payload
            }), 500  # Return 500 so our UI handles it

        cms_response = response.json()
        version.pushed_to_cms = True
        version.cms_id = cms_response.get('id')
        version.pushed_at = datetime.utcnow()
        version.pushed_by_id = current_user.id

        # Update parent content status
        content.status = 'published'

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'{version.ai_provider.title()} version pushed to CMS successfully',
            'cms_id': version.cms_id,
            'version_id': version.id
        })

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to push to CMS: {str(e)}', 'type': 'request_exception'}), 500
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@bp.route('/content/<int:id>/version/<int:version_id>/push-ghost', methods=['POST'])
@login_required
def push_version_to_ghost(id, version_id):
    """Push a specific version of content to Ghost CMS."""
    import markdown as md
    from app.ghost import create_ghost_post

    content = NewsContent.query.get_or_404(id)
    version = ContentVersion.query.get_or_404(version_id)

    if version.content_id != content.id:
        return jsonify({'error': 'Version does not belong to this content'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if version.pushed_to_ghost:
        return jsonify({'error': 'This version has already been pushed to Ghost'}), 400

    publication = content.publication
    if not publication.ghost_url or not publication.ghost_admin_api_key:
        return jsonify({'error': 'Ghost configuration not set for this publication'}), 400

    try:
        # Convert Markdown content to HTML
        html = md.markdown(version.content, extensions=['nl2br', 'fenced_code', 'tables'])

        # Build tags from keywords
        tags = [t.strip() for t in content.keywords.split(',')] if content.keywords else None

        result = create_ghost_post(
            ghost_url=publication.ghost_url,
            api_key=publication.ghost_admin_api_key,
            title=content.title,
            html=html,
            excerpt=version.teaser,
            tags=tags,
        )

        # Extract post info from response
        ghost_post = result.get('posts', [{}])[0]
        version.pushed_to_ghost = True
        version.ghost_post_id = ghost_post.get('id')
        version.ghost_post_url = ghost_post.get('url')
        version.ghost_pushed_at = datetime.utcnow()

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'{version.ai_provider.title()} version published to Ghost as draft',
            'ghost_post_id': version.ghost_post_id,
            'ghost_post_url': version.ghost_post_url,
        })

    except requests.exceptions.HTTPError as e:
        try:
            error_detail = e.response.json()
        except Exception:
            error_detail = e.response.text if e.response else str(e)
        return jsonify({'error': f'Ghost API error: {error_detail}'}), 500
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@bp.route('/content/<int:id>/select-version/<int:version_id>', methods=['POST'])
@login_required
def select_version(id, version_id):
    """Select a specific version as the preferred version for this content."""
    content = NewsContent.query.get_or_404(id)
    version = ContentVersion.query.get_or_404(version_id)

    # Verify version belongs to this content
    if version.content_id != content.id:
        return jsonify({'error': 'Version does not belong to this content'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    content.selected_version_id = version.id
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Selected {version.ai_provider.title()} version',
        'selected_version_id': version.id
    })


@bp.route('/content/<int:id>/version/<int:version_id>/edit', methods=['POST'])
@login_required
def edit_version(id, version_id):
    """Update editable fields on a content version."""
    content = NewsContent.query.get_or_404(id)
    version = ContentVersion.query.get_or_404(version_id)

    if version.content_id != content.id:
        return jsonify({'error': 'Version does not belong to this content'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    data = request.json or {}
    editable_fields = ('deck', 'teaser', 'summary', 'content', 'notes')
    updated = []
    for field in editable_fields:
        if field in data:
            setattr(version, field, data[field])
            updated.append(field)

    if not updated:
        return jsonify({'error': 'No editable fields provided'}), 400

    db.session.commit()
    return jsonify({'success': True, 'updated': updated})


def _revision_json(revision):
    return {
        'id': revision.id,
        'version_id': revision.version_id,
        'status': revision.status,
        'message': revision.message,
        'instructions': revision.instructions,
        'original': revision.original,
        'revised': revision.revised,
        'change_summary': revision.change_summary,
    }


def _get_revision_for_content(id, revision_id):
    """Load a content + revision pair, or return (None, error response)."""
    content = NewsContent.query.get_or_404(id)
    revision = VersionRevision.query.get_or_404(revision_id)
    if revision.content_id != content.id:
        return None, (jsonify({'error': 'Revision does not belong to this content'}), 400)
    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return None, (jsonify({'error': 'Access denied'}), 403)
    return revision, None


@bp.route('/content/<int:id>/version/<int:version_id>/revise', methods=['POST'])
@login_required
def revise_version(id, version_id):
    """Start an AI revision of a FINAL version from the editor's instructions."""
    from app.content_revision import MAX_INSTRUCTIONS_CHARS, snapshot_version

    content = NewsContent.query.get_or_404(id)
    version = ContentVersion.query.get_or_404(version_id)

    if version.content_id != content.id:
        return jsonify({'error': 'Version does not belong to this content'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if not version.is_final:
        return jsonify({'error': 'Only the audited FINAL version can be revised with AI'}), 400

    instructions = ((request.json or {}).get('instructions') or '').strip()
    if not instructions:
        return jsonify({'error': 'Tell the editor bot what to change'}), 400
    if len(instructions) > MAX_INSTRUCTIONS_CHARS:
        return jsonify({'error': f'Instructions are limited to {MAX_INSTRUCTIONS_CHARS} characters'}), 400

    revision = VersionRevision(
        content_id=content.id,
        version_id=version.id,
        requested_by_id=current_user.id,
        instructions=instructions,
        status='pending',
        original=snapshot_version(version),
    )
    db.session.add(revision)
    db.session.commit()

    from app.tasks import revise_content_version
    revise_content_version.delay(revision.id)

    return jsonify({'success': True, 'revision': _revision_json(revision)})


@bp.route('/content/<int:id>/revision/<int:revision_id>')
@login_required
def revision_status(id, revision_id):
    revision, error = _get_revision_for_content(id, revision_id)
    if error:
        return error
    return jsonify(_revision_json(revision))


@bp.route('/content/<int:id>/revision/<int:revision_id>/apply', methods=['POST'])
@login_required
def apply_revision(id, revision_id):
    """Write a completed revision's fields onto its version."""
    from app.content_revision import REVISABLE_FIELDS, snapshot_version

    revision, error = _get_revision_for_content(id, revision_id)
    if error:
        return error

    if revision.status != 'completed':
        return jsonify({'error': f"This revision is {revision.status} and can't be applied"}), 400

    version = revision.version
    if snapshot_version(version) != revision.original:
        return jsonify({'error': 'This version was edited after the revision was requested. '
                                 'Discard it and request a new revision.'}), 409

    for field in REVISABLE_FIELDS:
        setattr(version, field, revision.revised.get(field, ''))
    revision.status = 'applied'
    revision.applied_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'success': True})


@bp.route('/content/<int:id>/revision/<int:revision_id>/discard', methods=['POST'])
@login_required
def discard_revision(id, revision_id):
    revision, error = _get_revision_for_content(id, revision_id)
    if error:
        return error

    if revision.status == 'applied':
        return jsonify({'error': 'This revision has already been applied'}), 400

    revision.status = 'discarded'
    db.session.commit()
    return jsonify({'success': True})


@bp.route('/content/<int:id>/status', methods=['POST'])
@login_required
def update_status(id):
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    new_status = request.json.get('status')
    if new_status not in ['staged', 'approved', 'rejected', 'published']:
        return jsonify({'error': 'Invalid status'}), 400

    content.status = new_status

    # Handle rejection/approval reason
    if new_status in ['rejected', 'approved']:
        reason = request.json.get('rejection_reason', '').strip()
        content.rejection_reason = reason if reason else None
    else:
        content.rejection_reason = None

    db.session.commit()

    return jsonify({'success': True, 'status': content.status})


@bp.route('/publication/<int:id>/trigger-content-workflow', methods=['POST'])
@login_required
def trigger_content_workflow(id):
    publication = Publication.query.get_or_404(id)

    # Check access
    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        flash('Access denied', 'error')
        return redirect(url_for('main.dashboard'))

    workflow_url = current_app.config.get('N8N_CONTENT_WORKFLOW_URL')
    if not workflow_url:
        flash('Content workflow URL is not configured. Set N8N_CONTENT_WORKFLOW_URL environment variable.', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    # Create workflow run record
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=publication.id,
        triggered_by_id=current_user.id,
        workflow_type='content_generation',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
        # Fire-and-forget: use a very short timeout just to send the request
        requests.get(
            workflow_url,
            params={
                'publication_id': publication.id,
                'workflow_id': workflow_id
            },
            timeout=0.5
        )
    except requests.exceptions.Timeout:
        # Expected - we're not waiting for a response
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        flash(f'Failed to trigger workflow: {str(e)}', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    # Update status to running
    workflow_run.status = 'running'
    db.session.commit()

    return redirect(url_for('main.dashboard', publication_id=id, workflow_id=workflow_id))


@bp.route('/publication/<int:id>/trigger-candidate-content-workflow', methods=['POST'])
@login_required
def trigger_candidate_content_workflow(id):
    publication = Publication.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        flash('Access denied', 'error')
        return redirect(url_for('main.dashboard'))

    workflow_url = current_app.config.get('N8N_CANDIDATE_CONTENT_WORKFLOW_URL')
    if not workflow_url:
        flash('Candidate content workflow URL is not configured. Set N8N_CANDIDATE_CONTENT_WORKFLOW_URL environment variable.', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=publication.id,
        triggered_by_id=current_user.id,
        workflow_type='candidate_content_generation',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    # Build payload with optional author style guide
    payload = {
        'publication_id': publication.id,
        'workflow_id': workflow_id,
    }
    author_profile_id = request.form.get('author_profile_id', type=int)
    if author_profile_id:
        author = AuthorProfile.query.get(author_profile_id)
        if author and author.style_guide:
            payload['author_name'] = author.name
            payload['author_style_guide'] = author.style_guide

    try:
        requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
    except requests.exceptions.Timeout:
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        flash(f'Failed to trigger workflow: {str(e)}', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    workflow_run.status = 'running'
    db.session.commit()

    return redirect(url_for('main.dashboard', publication_id=id, workflow_id=workflow_id, workflow_type='candidate_content_generation'))


def _trigger_recipe_workflow(recipe):
    """Create a WorkflowRun and fire the recipe workflow at n8n.

    Returns the WorkflowRun. Raises ValueError if the workflow URL is not
    configured. Network failures mark the run failed and re-raise.
    """
    workflow_url = current_app.config.get('N8N_RECIPE_WORKFLOW_URL')
    if not workflow_url:
        raise ValueError('Recipe workflow URL is not configured. Set N8N_RECIPE_WORKFLOW_URL environment variable.')

    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=recipe.publication_id,
        triggered_by_id=current_user.id,
        workflow_type='content_recipe',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    payload = {
        'publication_id': recipe.publication_id,
        'workflow_id': workflow_id,
        'recipe_id': recipe.id,
        'recipe_name': recipe.name,
        'candidates': [
            {'id': c.id, 'url': c.url, 'title': c.title}
            for c in recipe.candidates
        ] + [
            {
                'id': s.id,
                'title': s.name,
                'url': url_for('main.serve_static_source', token=s.token, _external=True),
            }
            for s in recipe.static_sources
        ],
    }
    if recipe.author_profile and recipe.author_profile.style_guide:
        payload['author_name'] = recipe.author_profile.name
        payload['author_style_guide'] = recipe.author_profile.style_guide
    if recipe.override_instructions:
        payload['override_instructions'] = recipe.override_instructions

    try:
        requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
    except requests.exceptions.Timeout:
        # Fire-and-forget: n8n received it, the response just didn't return in time
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        recipe.last_run_at = datetime.utcnow()
        recipe.last_workflow_run_id = workflow_id
        db.session.commit()
        raise

    workflow_run.status = 'running'
    recipe.last_run_at = datetime.utcnow()
    recipe.last_workflow_run_id = workflow_id
    db.session.commit()
    return workflow_run


@bp.route('/publication/<int:id>/recipes', methods=['POST'])
@login_required
def create_content_recipe(id):
    """Create a Content Recipe from selected candidates and fire the workflow."""
    publication = Publication.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    candidate_ids = data.get('candidate_ids') or []
    static_source_ids = data.get('static_source_ids') or []
    author_profile_id = data.get('author_profile_id')
    override_instructions = (data.get('override_instructions') or '').strip() or None

    if not name:
        return jsonify({'error': 'Recipe name is required'}), 400
    if not candidate_ids and not static_source_ids:
        return jsonify({'error': 'Select at least one candidate or static source'}), 400

    # Only accept candidates / static sources belonging to this publication
    candidates = CandidateArticle.query.filter(
        CandidateArticle.id.in_(candidate_ids),
        CandidateArticle.publication_id == publication.id
    ).all() if candidate_ids else []
    sources = StaticSource.query.filter(
        StaticSource.id.in_(static_source_ids),
        StaticSource.publication_id == publication.id
    ).all() if static_source_ids else []

    if not candidates and not sources:
        return jsonify({'error': 'No valid candidates or static sources found for this publication'}), 400

    author = None
    if author_profile_id:
        author = AuthorProfile.query.filter_by(
            id=author_profile_id, publication_id=publication.id
        ).first()

    recipe = ContentRecipe(
        publication_id=publication.id,
        name=name,
        author_profile_id=author.id if author else None,
        override_instructions=override_instructions,
        created_by_id=current_user.id,
    )
    recipe.candidates = candidates
    recipe.static_sources = sources
    db.session.add(recipe)
    db.session.commit()

    try:
        run = _trigger_recipe_workflow(recipe)
    except ValueError as e:
        return jsonify({'error': str(e), 'recipe_id': recipe.id}), 400
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to trigger workflow: {str(e)}', 'recipe_id': recipe.id}), 500

    return jsonify({
        'success': True,
        'recipe_id': recipe.id,
        'workflow_id': run.id,
        'message': f'Recipe "{recipe.name}" created and sent for generation',
    })


@bp.route('/recipe/<int:recipe_id>/run', methods=['POST'])
@login_required
def run_content_recipe(recipe_id):
    """Re-run a saved Content Recipe."""
    recipe = ContentRecipe.query.get_or_404(recipe_id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(recipe.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if not recipe.candidates and not recipe.static_sources:
        return jsonify({'error': 'This recipe has no sources'}), 400

    try:
        run = _trigger_recipe_workflow(recipe)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to trigger workflow: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'workflow_id': run.id,
        'message': f'Recipe "{recipe.name}" sent for generation',
    })


@bp.route('/recipe/<int:recipe_id>/delete', methods=['POST'])
@login_required
def delete_content_recipe(recipe_id):
    """Delete a saved Content Recipe."""
    recipe = ContentRecipe.query.get_or_404(recipe_id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(recipe.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    db.session.delete(recipe)
    db.session.commit()
    return jsonify({'success': True})


@bp.route('/source/<token>')
def serve_static_source(token):
    """Public, unauthenticated HTML view of a static source so n8n (or its
    scraper, e.g. Firecrawl) can fetch it like any other source URL.

    No login/API key: the unguessable token is the access control.
    """
    import markdown as md

    source = StaticSource.query.filter_by(token=token).first_or_404()
    body_html = md.markdown(source.content or '', extensions=['nl2br', 'fenced_code', 'tables'])
    return render_template('main/static_source.html', source=source, body_html=body_html)


@bp.route('/publication/<int:id>/static-sources', methods=['POST'])
@login_required
def create_static_source(id):
    """Create a reusable static source for a publication."""
    publication = Publication.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    content = (data.get('content') or '').strip()

    if not name:
        return jsonify({'error': 'Name is required'}), 400
    if not content:
        return jsonify({'error': 'Content is required'}), 400

    source = StaticSource(
        publication_id=publication.id,
        name=name,
        content=content,
        created_by_id=current_user.id,
    )
    db.session.add(source)
    db.session.commit()

    return jsonify({
        'success': True,
        'id': source.id,
        'name': source.name,
        'url': url_for('main.serve_static_source', token=source.token, _external=True),
    })


@bp.route('/static-source/<int:source_id>/delete', methods=['POST'])
@login_required
def delete_static_source(source_id):
    """Delete a static source."""
    source = StaticSource.query.get_or_404(source_id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(source.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    db.session.delete(source)
    db.session.commit()
    return jsonify({'success': True})


@bp.route('/publication/<int:id>/submit-url-workflow', methods=['POST'])
@login_required
def submit_url_workflow(id):
    publication = Publication.query.get_or_404(id)

    # Check access
    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        flash('Access denied', 'error')
        return redirect(url_for('main.dashboard'))

    source_url = request.form.get('source_url', '').strip()
    if not source_url or not source_url.startswith(('http://', 'https://')):
        flash('Please enter a valid URL starting with http:// or https://', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    workflow_url = current_app.config.get('N8N_SUBMIT_URL_WORKFLOW_URL')
    if not workflow_url:
        flash('Submit URL workflow is not configured. Set N8N_SUBMIT_URL_WORKFLOW_URL environment variable.', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    # Create workflow run record
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=publication.id,
        triggered_by_id=current_user.id,
        workflow_type='submit_url',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    # Build payload with optional author style guide
    payload = {
        'publication_id': publication.id,
        'workflow_id': workflow_id,
        'source_url': source_url,
    }
    author_profile_id = request.form.get('author_profile_id', type=int)
    if author_profile_id:
        author = AuthorProfile.query.get(author_profile_id)
        if author and author.style_guide:
            payload['author_name'] = author.name
            payload['author_style_guide'] = author.style_guide

    try:
        requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
    except requests.exceptions.Timeout:
        # Expected for async workflows
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        flash(f'Failed to trigger workflow: {str(e)}', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    # Update status to running
    workflow_run.status = 'running'
    db.session.commit()

    return redirect(url_for('main.dashboard', publication_id=id, workflow_id=workflow_id, workflow_type='submit_url'))


@bp.route('/content/<int:id>/generate-image', methods=['POST'])
@login_required
def generate_image(id):
    """Trigger image generation workflow for a content item."""
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    workflow_url = current_app.config.get('N8N_IMAGE_WORKFLOW_URL')
    if not workflow_url:
        return jsonify({'error': 'Image workflow URL is not configured. Set N8N_IMAGE_WORKFLOW_URL environment variable.'}), 400

    # Create workflow run record
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=content.publication_id,
        triggered_by_id=current_user.id,
        workflow_type='image_generation',
        status='pending',
        message=f'content_id:{content.id}'  # Store content_id for the callback
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
        # Get summary from selected version or fall back to legacy fields
        version = content.selected_version
        if version:
            summary = version.summary or version.teaser or version.deck or ''
        else:
            summary = content.summary or content.teaser or content.deck or ''

        # POST to n8n with article summary
        payload = {
            'workflow_id': workflow_id,
            'content_id': content.id,
            'title': content.title,
            'summary': summary
        }

        response = requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        # Timeout is acceptable for async workflows
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        return jsonify({'error': f'Failed to trigger image workflow: {str(e)}'}), 500

    # Update status to running
    workflow_run.status = 'running'
    db.session.commit()

    return jsonify({
        'success': True,
        'workflow_id': workflow_id,
        'message': 'Image generation started'
    })


@bp.route('/content/<int:id>/audit', methods=['POST'])
@login_required
def trigger_audit(id):
    """Trigger audit workflow to patch and score article versions."""
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    # Check if there are versions to audit
    if not content.versions:
        return jsonify({'error': 'No versions to audit. This article has no AI-generated versions.'}), 400

    workflow_url = current_app.config.get('N8N_AUDIT_WORKFLOW_URL')
    if not workflow_url:
        return jsonify({'error': 'Audit workflow URL is not configured. Set N8N_AUDIT_WORKFLOW_URL environment variable.'}), 400

    # Create workflow run record
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=content.publication_id,
        triggered_by_id=current_user.id,
        workflow_type='audit',
        status='pending',
        message=f'content_id:{content.id}'
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
        # Build payload with all versions
        versions_payload = []
        for version in content.versions:
            versions_payload.append({
                'version_id': version.id,
                'ai_provider': version.ai_provider,
                'ai_model': version.ai_model,
                'quality_score': version.quality_score,
                'is_final': version.is_final,
                'deck': version.deck,
                'teaser': version.teaser,
                'body': version.content,
                'summary': version.summary,
                'notes': version.notes
            })

        payload = {
            'workflow_id': workflow_id,
            'article_id': content.id,
            'title': content.title,
            'source_url': content.source_url,
            'source_name': content.source_name,
            'keywords': content.keywords,
            'versions': versions_payload
        }

        response = requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        # Timeout is acceptable for async workflows
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        return jsonify({'error': f'Failed to trigger audit workflow: {str(e)}'}), 500

    # Update status to running
    workflow_run.status = 'running'
    db.session.commit()

    return jsonify({
        'success': True,
        'workflow_id': workflow_id,
        'message': 'Audit workflow started'
    })


@bp.route('/content/<int:id>/audit-data')
@login_required
def get_audit_data(id):
    """Returns all audit and patched data for an article's versions."""
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    # Get all version audits for this content
    version_audits = VersionAudit.query.filter_by(content_id=id).order_by(VersionAudit.created_at.desc()).all()

    # Get all patched versions for this content
    patched_versions = PatchedVersion.query.filter_by(content_id=id).order_by(PatchedVersion.created_at.desc()).all()

    return jsonify({
        'version_audits': [
            {
                'id': va.id,
                'version_id': va.version_id,
                'ai_provider': va.ai_provider,
                'ai_model': va.ai_model,
                'overall_risk': va.overall_risk,
                'risk_summary': va.risk_summary,
                'original_draft': va.original_draft,
                'issues': va.issues,
                'created_at': va.created_at.isoformat() if va.created_at else None
            }
            for va in version_audits
        ],
        'patched_versions': [
            {
                'id': pv.id,
                'version_id': pv.version_id,
                'ai_provider': pv.ai_provider,
                'ai_model': pv.ai_model,
                'patched_draft': pv.patched_draft,
                'created_at': pv.created_at.isoformat() if pv.created_at else None
            }
            for pv in patched_versions
        ]
    })


CANDIDATE_SORT_COLUMNS = {
    'score': CandidateArticle.relevance_score,
    'title': CandidateArticle.title,
    'source': NewsSource.name,
    'published': CandidateArticle.published_date,
    'discovered': CandidateArticle.discovered_at,
    'status': CandidateArticle.status,
}
CANDIDATE_SORT_DEFAULTS = {'score': 'desc', 'published': 'desc', 'discovered': 'desc'}


@bp.route('/candidates')
@login_required
def candidates():
    """Paginated list of candidate articles with filters."""
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'new')
    min_score = request.args.get('min_score', 0, type=float)
    coverage = request.args.get('coverage') or None
    sort = request.args.get('sort', 'score')
    direction = request.args.get('direction', 'desc' if sort == 'score' else 'asc')
    if sort not in CANDIDATE_SORT_COLUMNS:
        sort = 'score'
    if direction not in ('asc', 'desc'):
        direction = 'desc'

    # Get available publications for the user
    if current_user.has_role('admin'):
        publications = Publication.query.filter_by(is_active=True).order_by(Publication.id).all()
    else:
        publications = [p for p in current_user.publications if p.is_active]

    publication_id = resolve_publication_id(publications)
    current_publication = Publication.query.get(publication_id) if publication_id else None

    query = CandidateArticle.query

    if sort == 'source':
        query = query.outerjoin(NewsSource, CandidateArticle.news_source_id == NewsSource.id)

    if publication_id:
        query = query.filter(CandidateArticle.publication_id == publication_id)
    elif not current_user.has_role('admin') and current_user.publications:
        pub_ids = current_user.get_publication_ids()
        query = query.filter(CandidateArticle.publication_id.in_(pub_ids))

    if status != 'all':
        query = query.filter(CandidateArticle.status == status)

    if min_score > 0:
        query = query.filter(CandidateArticle.relevance_score >= min_score)

    from app.research.house_content import apply_coverage_filter, COVERAGE_FILTERS
    if coverage not in COVERAGE_FILTERS:
        coverage = None
    query = apply_coverage_filter(query, coverage)

    sort_col = CANDIDATE_SORT_COLUMNS[sort]
    sort_expr = sort_col.desc() if direction == 'desc' else sort_col.asc()
    # Tiebreak by id for stable pagination across columns with duplicates
    candidates_page = query.order_by(sort_expr, CandidateArticle.id.desc()).paginate(
        page=page, per_page=current_app.config['ITEMS_PER_PAGE'], error_out=False
    )

    author_profiles = []
    if publication_id:
        author_profiles = AuthorProfile.query.filter_by(
            publication_id=publication_id, is_active=True
        ).order_by(AuthorProfile.is_default.desc(), AuthorProfile.name).all()

    return render_template('main/candidates.html', title='Candidate Articles',
                           candidates=candidates_page, status=status, min_score=min_score,
                           coverage=coverage, coverage_filters=COVERAGE_FILTERS,
                           sort=sort, direction=direction,
                           publications=publications, current_publication=current_publication,
                           author_profiles=author_profiles)


@bp.route('/publication/<int:id>/trigger-research', methods=['POST'])
@login_required
def trigger_research(id):
    """Trigger research task from UI."""
    publication = Publication.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        flash('Access denied', 'error')
        return redirect(url_for('main.candidates'))

    from app.tasks import start_research
    run, created = start_research(publication.id, trigger='manual')

    if request.accept_mimetypes.best == 'application/json':
        return jsonify({'created': created, 'run': run.to_dict()}), (202 if created else 200)

    if created:
        flash(f'Research started for {publication.name}. Progress is shown below.', 'success')
    else:
        flash(f'Research is already running for {publication.name}.', 'info')
    return redirect(url_for('main.candidates', publication_id=id))


@bp.route('/publication/<int:id>/research-status')
@login_required
def research_status(id):
    """Latest research run for a publication, polled by the candidates page."""
    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        return jsonify({'error': 'Access denied'}), 403

    from app.research.progress import latest_run
    run = latest_run(id)
    return jsonify({'run': run.to_dict() if run else None})


@bp.route('/candidate/<int:id>/status', methods=['POST'])
@login_required
def update_candidate_status(id):
    """AJAX status update for a candidate (select/reject)."""
    candidate = CandidateArticle.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(candidate.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    new_status = request.json.get('status')
    if new_status not in ('selected', 'rejected', 'new'):
        return jsonify({'error': 'Invalid status'}), 400

    candidate.status = new_status
    if new_status == 'rejected':
        candidate.rejection_reason = (request.json.get('rejection_reason') or '').strip() or None
    else:
        candidate.rejection_reason = None
    db.session.commit()

    return jsonify({'success': True, 'status': candidate.status})


# Which current statuses a given target transition is allowed from.
# Mirrors the per-row button visibility in candidates.html.
_BULK_STATUS_SOURCES = {
    'selected': ('new', 'rejected'),
    'rejected': ('new', 'selected'),
    'new': ('selected', 'rejected'),
}


@bp.route('/candidates/bulk-status', methods=['POST'])
@login_required
def bulk_candidate_status():
    """Apply a status change (select/reject/reset) to many candidates at once.

    Candidates whose current status doesn't support the transition, or that the
    user can't access, are skipped rather than failing the whole batch.
    """
    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    if new_status not in _BULK_STATUS_SOURCES:
        return jsonify({'error': 'Invalid status'}), 400

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'No candidates selected'}), 400

    rejection_reason = None
    if new_status == 'rejected':
        rejection_reason = (data.get('rejection_reason') or '').strip() or None

    allowed_sources = _BULK_STATUS_SOURCES[new_status]
    candidates = CandidateArticle.query.filter(CandidateArticle.id.in_(ids)).all()

    updated = 0
    skipped = 0
    for candidate in candidates:
        if not current_user.has_role('admin') and not current_user.has_publication_access(candidate.publication_id):
            skipped += 1
            continue
        if candidate.status not in allowed_sources:
            skipped += 1
            continue
        candidate.status = new_status
        candidate.rejection_reason = rejection_reason if new_status == 'rejected' else None
        updated += 1

    # Any ids not found in the DB are also skipped.
    skipped += len(ids) - len(candidates)
    db.session.commit()

    return jsonify({'success': True, 'updated': updated, 'skipped': skipped})


@bp.route('/candidate/<int:id>/create-article', methods=['POST'])
@login_required
def create_article_from_candidate(id):
    """Send a candidate article's URL to the n8n submit-URL workflow."""
    candidate = CandidateArticle.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(candidate.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    workflow_url = current_app.config.get('N8N_SUBMIT_URL_WORKFLOW_URL')
    if not workflow_url:
        return jsonify({'error': 'Submit URL workflow not configured'}), 500

    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=candidate.publication_id,
        triggered_by_id=current_user.id,
        workflow_type='submit_url',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    # Build payload with optional author style guide
    payload = {
        'publication_id': candidate.publication_id,
        'workflow_id': workflow_id,
        'source_url': candidate.url,
    }
    data = request.get_json(silent=True) or {}
    author_profile_id = data.get('author_profile_id')
    if author_profile_id:
        author = AuthorProfile.query.get(author_profile_id)
        if author and author.style_guide:
            payload['author_name'] = author.name
            payload['author_style_guide'] = author.style_guide

    try:
        requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
    except requests.exceptions.Timeout:
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        return jsonify({'error': f'Failed to trigger workflow: {e}'}), 500

    workflow_run.status = 'running'
    candidate.status = 'processed'
    db.session.commit()

    return jsonify({'success': True, 'workflow_id': workflow_id})


@bp.route('/candidate/<int:id>/detail')
@login_required
def candidate_detail(id):
    """Return candidate detail as JSON for the detail modal."""
    candidate = CandidateArticle.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(candidate.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    metadata = candidate.extra_metadata or {}

    return jsonify({
        'id': candidate.id,
        'url': candidate.url,
        'title': candidate.title,
        'snippet': candidate.snippet,
        'author': candidate.author,
        'published_date': candidate.published_date.isoformat() if candidate.published_date else None,
        'relevance_score': candidate.relevance_score,
        'keyword_score': candidate.keyword_score,
        'recency_score': candidate.recency_score,
        'source_weight': candidate.source_weight,
        'status': candidate.status,
        'rejection_reason': candidate.rejection_reason,
        'source_name': candidate.news_source.name if candidate.news_source else None,
        'source_type': candidate.news_source.source_type if candidate.news_source else None,
        'discovered_at': candidate.discovered_at.isoformat() if candidate.discovered_at else None,
        'triage_verdict': metadata.get('triage_verdict'),
        'triage_reasoning': metadata.get('triage_reasoning'),
        'house_coverage': metadata.get('house_coverage'),
        'content_source': metadata.get('content_source'),
        'content_length': metadata.get('content_length'),
        'content_format': metadata.get('content_format'),
        'feed_url': metadata.get('feed_url'),
        'enrichment_error': metadata.get('enrichment_error'),
        'metadata': metadata,
    })