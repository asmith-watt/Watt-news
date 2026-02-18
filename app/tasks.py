"""Celery tasks for scheduled content generation and research."""
import logging
import uuid
from datetime import datetime, timedelta
import requests
from flask import current_app

from app.celery import celery
from app import db
from app.models import Publication, WorkflowRun, CandidateArticle, NewsSource

logger = logging.getLogger(__name__)


@celery.task(name='app.tasks.trigger_scheduled_content_workflow')
def trigger_scheduled_content_workflow(publication_id):
    """
    Trigger the n8n content generation workflow for a publication.
    Called by the scheduler or manually.
    """
    publication = Publication.query.get(publication_id)
    if not publication:
        return {'error': f'Publication {publication_id} not found'}

    if not publication.is_active:
        return {'error': f'Publication {publication_id} is not active'}

    workflow_url = current_app.config.get('N8N_CONTENT_WORKFLOW_URL')
    if not workflow_url:
        return {'error': 'N8N_CONTENT_WORKFLOW_URL not configured'}

    # Create workflow run record (triggered_by_id=None for system-triggered)
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=publication.id,
        triggered_by_id=None,  # System-triggered (scheduled)
        workflow_type='content_generation',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
        # Fire-and-forget request to n8n
        requests.get(
            workflow_url,
            params={
                'publication_id': publication.id,
                'workflow_id': workflow_id
            },
            timeout=5
        )
        workflow_run.status = 'running'
        db.session.commit()

        return {
            'success': True,
            'workflow_id': workflow_id,
            'publication_id': publication_id
        }

    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        return {'error': str(e), 'workflow_id': workflow_id}


@celery.task(name='app.tasks.check_publication_schedules')
def check_publication_schedules():
    """
    Check if any publications are due for scheduled content generation.
    Runs every minute via Celery Beat.
    """
    now = datetime.utcnow()

    # Find publications that are:
    # - schedule_enabled = True
    # - is_active = True
    # - next_scheduled_run <= now
    due_publications = Publication.query.filter(
        Publication.schedule_enabled == True,
        Publication.is_active == True,
        Publication.next_scheduled_run <= now
    ).all()

    triggered_count = 0
    for publication in due_publications:
        # Trigger the workflow
        trigger_scheduled_content_workflow.delay(publication.id)

        # Update scheduling timestamps
        publication.last_scheduled_run = now
        publication.next_scheduled_run = calculate_next_run(publication)
        triggered_count += 1

    if triggered_count > 0:
        db.session.commit()

    return {
        'checked_at': now.isoformat(),
        'triggered_count': triggered_count
    }


def calculate_next_run(publication):
    """
    Calculate the next scheduled run time for a publication.
    """
    if not publication.schedule_time:
        return None

    now = datetime.utcnow()

    # Parse schedule time (HH:MM format)
    try:
        hour, minute = map(int, publication.schedule_time.split(':'))
    except (ValueError, AttributeError):
        return None

    if publication.schedule_frequency == 'daily':
        # Next occurrence at the specified time
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    elif publication.schedule_frequency == 'weekly':
        # Next occurrence on the specified day at the specified time
        target_day = publication.schedule_day_of_week or 0  # Default to Monday
        current_day = now.weekday()

        # Calculate days until target day
        days_ahead = target_day - current_day
        if days_ahead < 0:
            days_ahead += 7

        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        next_run += timedelta(days=days_ahead)

        # If it's the target day but the time has passed, move to next week
        if days_ahead == 0 and next_run <= now:
            next_run += timedelta(days=7)

        return next_run

    return None


@celery.task(name='app.tasks.check_research_schedules')
def check_research_schedules():
    """
    Check if any publications are due for research scanning.
    Runs every 15 minutes via Celery Beat. Dispatches research for publications
    whose last_research_run is null or older than 6 hours.
    """
    now = datetime.utcnow()
    six_hours_ago = now - timedelta(hours=6)

    due_publications = Publication.query.filter(
        Publication.is_active == True,
        db.or_(
            Publication.last_research_run == None,
            Publication.last_research_run <= six_hours_ago,
        )
    ).all()

    dispatched = 0
    for pub in due_publications:
        # Only dispatch if the publication has active sources
        has_sources = NewsSource.query.filter_by(
            publication_id=pub.id, is_active=True
        ).first() is not None
        if has_sources:
            research_publication_sources.delay(pub.id)
            dispatched += 1

    return {
        'checked_at': now.isoformat(),
        'dispatched': dispatched,
    }


@celery.task(name='app.tasks.research_publication_sources', bind=True, max_retries=2, default_retry_delay=60)
def research_publication_sources(self, publication_id):
    """
    Scan all active sources for a publication, discover candidate articles,
    deduplicate, score, and store them.
    """
    from app.research.scrapers import get_scraper
    from app.research.dedup import url_hash, is_duplicate_candidate, is_already_content
    from app.research.scoring import score_candidate

    publication = Publication.query.get(publication_id)
    if not publication or not publication.is_active:
        return {'error': f'Publication {publication_id} not found or inactive'}

    sources = NewsSource.query.filter_by(
        publication_id=publication_id, is_active=True
    ).all()

    stats = {
        'sources_scanned': 0,
        'total_discovered': 0,
        'new_candidates': 0,
        'skipped_duplicates': 0,
        'errors': 0,
    }

    for source in sources:
        scraper = get_scraper(source.source_type)
        if not scraper:
            logger.info(f"No scraper for source type '{source.source_type}', skipping {source.name}")
            continue

        try:
            items = scraper.scrape(source)
            stats['sources_scanned'] += 1
            stats['total_discovered'] += len(items)
        except Exception as e:
            logger.error(f"Scraper error for source {source.name} ({source.id}): {e}")
            stats['errors'] += 1
            continue

        for item in items:
            try:
                hash_val = url_hash(item.url)

                if is_duplicate_candidate(hash_val, publication_id):
                    stats['skipped_duplicates'] += 1
                    continue

                if is_already_content(item.url, publication_id):
                    stats['skipped_duplicates'] += 1
                    continue

                scores = score_candidate(
                    title=item.title,
                    snippet=item.snippet,
                    published_date=item.published_date,
                    source_type=source.source_type,
                    industry_description=publication.industry_description or '',
                    source_keywords=source.keywords or '',
                )

                candidate = CandidateArticle(
                    publication_id=publication_id,
                    news_source_id=source.id,
                    url=item.url,
                    url_hash=hash_val,
                    title=item.title,
                    snippet=item.snippet,
                    author=item.author,
                    published_date=item.published_date,
                    relevance_score=scores['relevance_score'],
                    keyword_score=scores['keyword_score'],
                    recency_score=scores['recency_score'],
                    source_weight=scores['source_weight'],
                    status='new',
                    extra_metadata=item.metadata or {},
                )
                db.session.add(candidate)
                stats['new_candidates'] += 1

            except Exception as e:
                logger.error(f"Error processing item {item.url}: {e}")
                stats['errors'] += 1
                continue

    # Commit all new candidates and update last_research_run
    try:
        publication.last_research_run = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to commit research results for pub {publication_id}: {e}")
        raise self.retry(exc=e)

    logger.info(f"Research complete for publication {publication_id}: {stats}")
    return stats
