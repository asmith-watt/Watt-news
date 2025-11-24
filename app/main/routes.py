from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app.models import NewsContent, Publication
from app.main import bp
import requests


@bp.route('/')
@login_required
def index():
    return redirect(url_for('main.dashboard'))


@bp.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'staged')

    query = NewsContent.query

    if not current_user.has_role('admin') and current_user.publications:
        pub_ids = current_user.get_publication_ids()
        query = query.filter(NewsContent.publication_id.in_(pub_ids))

    if status != 'all':
        query = query.filter_by(status=status)

    content = query.order_by(NewsContent.created_at.desc()).paginate(
        page=page, per_page=current_app.config['ITEMS_PER_PAGE'], error_out=False
    )

    return render_template('main/dashboard.html', title='Dashboard', content=content, status=status)


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
        payload = {
            'title': content.title,
            'content': content.content,
            'summary': content.summary,
            'author': content.author,
            'source_url': content.source_url,
            'image_url': content.image_url,
            'published_date': content.published_date.isoformat() if content.published_date else None
        }

        headers = {
            'Authorization': f'Bearer {publication.cms_api_key}',
            'Content-Type': 'application/json'
        }

        response = requests.post(publication.cms_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        cms_response = response.json()
        content.pushed_to_cms = True
        content.cms_id = cms_response.get('id')
        content.pushed_at = datetime.utcnow()
        content.pushed_by_id = current_user.id
        content.status = 'published'

        db.session.commit()

        return jsonify({'success': True, 'message': 'Content pushed to CMS successfully', 'cms_id': content.cms_id})

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to push to CMS: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


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
    db.session.commit()

    return jsonify({'success': True, 'status': content.status})