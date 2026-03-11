from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from flask_wtf.csrf import generate_csrf
from app import db
from app.models import (
    Publication, NewsContent, Newsletter, NewsletterTemplate, NewsletterItem
)
from app.newsletter import bp


def _get_user_publications():
    """Return publications accessible to the current user."""
    if current_user.has_role('admin'):
        return Publication.query.filter_by(is_active=True).all()
    return [p for p in current_user.publications if p.is_active]


@bp.route('')
@login_required
def index():
    publications = _get_user_publications()
    publication_id = request.args.get('publication_id', type=int)

    current_publication = None
    if publication_id:
        current_publication = Publication.query.get(publication_id)
    elif publications:
        current_publication = publications[0]

    newsletters = []
    if current_publication:
        newsletters = Newsletter.query.filter_by(
            publication_id=current_publication.id
        ).order_by(Newsletter.updated_at.desc()).all()

    return render_template('newsletter/list.html',
                           title='Newsletters',
                           publications=publications,
                           current_publication=current_publication,
                           newsletters=newsletters)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    publications = _get_user_publications()
    publication_id = request.args.get('publication_id', type=int)

    current_publication = None
    if publication_id:
        current_publication = Publication.query.get(publication_id)
    elif publications:
        current_publication = publications[0]

    if not current_publication:
        flash('No publication selected.', 'error')
        return redirect(url_for('newsletter.index'))

    templates = NewsletterTemplate.query.filter_by(
        publication_id=current_publication.id, is_active=True
    ).all()

    if request.method == 'POST':
        template_id = request.form.get('template_id', type=int)
        name = request.form.get('name', '').strip()

        if not template_id or not name:
            flash('Please provide a name and select a template.', 'error')
            return render_template('newsletter/new.html',
                                   title='New Newsletter',
                                   current_publication=current_publication,
                                   templates=templates,
                                   csrf_token=generate_csrf())

        template = NewsletterTemplate.query.get(template_id)
        if not template or template.publication_id != current_publication.id:
            flash('Invalid template.', 'error')
            return redirect(url_for('newsletter.new', publication_id=current_publication.id))

        newsletter = Newsletter(
            publication_id=current_publication.id,
            template_id=template_id,
            name=name,
            created_by_id=current_user.id,
        )
        db.session.add(newsletter)
        db.session.commit()
        flash('Newsletter created.', 'success')
        return redirect(url_for('newsletter.edit', id=newsletter.id))

    return render_template('newsletter/new.html',
                           title='New Newsletter',
                           current_publication=current_publication,
                           templates=templates,
                           csrf_token=generate_csrf())


@bp.route('/<int:id>/edit')
@login_required
def edit(id):
    newsletter = Newsletter.query.get_or_404(id)
    publication = newsletter.publication
    template = newsletter.template

    # Get available articles (approved/published) for this publication
    available_articles = NewsContent.query.filter(
        NewsContent.publication_id == publication.id,
        NewsContent.status.in_(['approved', 'published'])
    ).order_by(NewsContent.created_at.desc()).all()

    # Get current newsletter items
    items = NewsletterItem.query.filter_by(
        newsletter_id=newsletter.id
    ).order_by(NewsletterItem.sort_order).all()

    # Build set of already-added content IDs
    added_ids = {item.news_content_id for item in items}

    return render_template('newsletter/edit.html',
                           title=f'Edit: {newsletter.name}',
                           newsletter=newsletter,
                           publication=publication,
                           template=template,
                           available_articles=available_articles,
                           items=items,
                           added_ids=added_ids)


@bp.route('/<int:id>/save', methods=['POST'])
@login_required
def save(id):
    newsletter = Newsletter.query.get_or_404(id)
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Update newsletter fields
    if 'name' in data:
        newsletter.name = data['name']
    if 'intro_text' in data:
        newsletter.intro_text = data['intro_text']
    if 'status' in data and data['status'] in ('draft', 'ready'):
        newsletter.status = data['status']

    # Update items
    if 'items' in data:
        # Remove existing items
        NewsletterItem.query.filter_by(newsletter_id=newsletter.id).delete()

        for i, item_data in enumerate(data['items']):
            item = NewsletterItem(
                newsletter_id=newsletter.id,
                news_content_id=item_data['news_content_id'],
                display_mode=item_data.get('display_mode', 'title_teaser'),
                sort_order=i,
                custom_summary=item_data.get('custom_summary'),
            )
            db.session.add(item)

    db.session.commit()
    return jsonify({'success': True})


@bp.route('/<int:id>/generate-summary', methods=['POST'])
@login_required
def generate_summary(id):
    newsletter = Newsletter.query.get_or_404(id)
    data = request.get_json()

    news_content_id = data.get('news_content_id')
    if not news_content_id:
        return jsonify({'error': 'news_content_id required'}), 400

    content = NewsContent.query.get(news_content_id)
    if not content:
        return jsonify({'error': 'Content not found'}), 404

    # Use the best available content text
    display = content.get_display_version()
    content_text = display.content or display.summary or content.teaser or ''

    from app.newsletter.ai import generate_article_summary
    summary = generate_article_summary(content_text, content.title, newsletter.publication)

    if not summary:
        return jsonify({'error': 'Failed to generate summary'}), 500

    return jsonify({'success': True, 'summary': summary})


@bp.route('/<int:id>/generate-intro', methods=['POST'])
@login_required
def generate_intro(id):
    newsletter = Newsletter.query.get_or_404(id)
    data = request.get_json()

    news_content_ids = data.get('news_content_ids', [])
    if not news_content_ids:
        return jsonify({'error': 'news_content_ids required'}), 400

    articles = []
    for cid in news_content_ids:
        content = NewsContent.query.get(cid)
        if content:
            articles.append({
                'title': content.title,
                'teaser': content.teaser or '',
            })

    if not articles:
        return jsonify({'error': 'No valid articles found'}), 400

    from app.newsletter.ai import generate_newsletter_intro
    intro = generate_newsletter_intro(articles, newsletter.publication)

    if not intro:
        return jsonify({'error': 'Failed to generate intro'}), 500

    return jsonify({'success': True, 'intro': intro})


@bp.route('/<int:id>/preview')
@login_required
def preview(id):
    newsletter = Newsletter.query.get_or_404(id)
    rendered_html = _render_newsletter_html(newsletter)
    return render_template('newsletter/preview.html',
                           title=f'Preview: {newsletter.name}',
                           newsletter=newsletter,
                           rendered_html=rendered_html)


@bp.route('/<int:id>/html')
@login_required
def html(id):
    newsletter = Newsletter.query.get_or_404(id)
    rendered_html = _render_newsletter_html(newsletter)
    return rendered_html, 200, {'Content-Type': 'text/html; charset=utf-8'}


@bp.route('/<int:id>/push-ghost', methods=['POST'])
@login_required
def push_to_ghost(id):
    """Push a newsletter to Ghost CMS."""
    import requests as req
    from app.ghost import create_ghost_newsletter_post

    newsletter = Newsletter.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(newsletter.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if newsletter.pushed_to_ghost:
        return jsonify({'error': 'This newsletter has already been pushed to Ghost'}), 400

    publication = newsletter.publication
    if not publication.ghost_url or not publication.ghost_admin_api_key:
        return jsonify({'error': 'Ghost configuration not set for this publication'}), 400

    try:
        # Render newsletter HTML (already fully rendered with inline styles)
        html = _render_newsletter_html(newsletter)

        result = create_ghost_newsletter_post(
            ghost_url=publication.ghost_url,
            api_key=publication.ghost_admin_api_key,
            title=newsletter.name,
            html=html,
            newsletter_slug=publication.ghost_newsletter_slug,
        )

        # Extract post info from response
        ghost_post = result.get('posts', [{}])[0]
        newsletter.pushed_to_ghost = True
        newsletter.ghost_post_id = ghost_post.get('id')
        newsletter.ghost_post_url = ghost_post.get('url')
        newsletter.ghost_pushed_at = datetime.utcnow()

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Newsletter published to Ghost as draft',
            'ghost_post_id': newsletter.ghost_post_id,
            'ghost_post_url': newsletter.ghost_post_url,
        })

    except req.exceptions.HTTPError as e:
        try:
            error_detail = e.response.json()
        except Exception:
            error_detail = e.response.text if e.response else str(e)
        return jsonify({'error': f'Ghost API error: {error_detail}'}), 500
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    newsletter = Newsletter.query.get_or_404(id)
    pub_id = newsletter.publication_id
    db.session.delete(newsletter)
    db.session.commit()
    flash('Newsletter deleted.', 'success')
    return redirect(url_for('newsletter.index', publication_id=pub_id))


def _render_newsletter_html(newsletter):
    """Render the full newsletter HTML using the _render.html template."""
    from datetime import date
    from app.sponsy import fetch_slot_html

    template = newsletter.template
    publication = newsletter.publication
    items = NewsletterItem.query.filter_by(
        newsletter_id=newsletter.id
    ).order_by(NewsletterItem.sort_order).all()

    top_ad_html = None
    mid_ad_html = None

    if publication.sponsy_api_key and publication.sponsy_publication_id:
        today = date.today().isoformat()
        if template.sponsy_top_placement_id:
            top_ad_html = fetch_slot_html(
                publication.sponsy_api_key,
                publication.sponsy_publication_id,
                template.sponsy_top_placement_id,
                today,
            )
        if template.sponsy_mid_placement_id:
            mid_ad_html = fetch_slot_html(
                publication.sponsy_api_key,
                publication.sponsy_publication_id,
                template.sponsy_mid_placement_id,
                today,
            )

    return render_template('newsletter/_render.html',
                           newsletter=newsletter,
                           template=template,
                           items=items,
                           top_ad_html=top_ad_html,
                           mid_ad_html=mid_ad_html,
                           sponsy_mid_position=template.sponsy_mid_position or 3)
