"""Celery tasks for scheduled content generation and research."""
import logging
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse
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


def calculate_next_candidate_run(publication):
    """Calculate the next candidate content generation run time."""
    if not publication.candidate_schedule_time:
        return None

    now = datetime.utcnow()

    try:
        hour, minute = map(int, publication.candidate_schedule_time.split(':'))
    except (ValueError, AttributeError):
        return None

    if publication.candidate_schedule_frequency == 'daily':
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    elif publication.candidate_schedule_frequency == 'weekly':
        target_day = publication.candidate_schedule_day_of_week or 0
        current_day = now.weekday()
        days_ahead = target_day - current_day
        if days_ahead < 0:
            days_ahead += 7

        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        next_run += timedelta(days=days_ahead)

        if days_ahead == 0 and next_run <= now:
            next_run += timedelta(days=7)

        return next_run

    return None


@celery.task(name='app.tasks.trigger_scheduled_candidate_content_workflow')
def trigger_scheduled_candidate_content_workflow(publication_id):
    """Trigger the n8n candidate content generation workflow for a publication."""
    publication = Publication.query.get(publication_id)
    if not publication:
        return {'error': f'Publication {publication_id} not found'}

    if not publication.is_active:
        return {'error': f'Publication {publication_id} is not active'}

    workflow_url = current_app.config.get('N8N_CANDIDATE_CONTENT_WORKFLOW_URL')
    if not workflow_url:
        return {'error': 'N8N_CANDIDATE_CONTENT_WORKFLOW_URL not configured'}

    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=publication.id,
        triggered_by_id=None,
        workflow_type='candidate_content_generation',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
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


@celery.task(name='app.tasks.check_candidate_content_schedules')
def check_candidate_content_schedules():
    """Check if any publications are due for scheduled candidate content generation."""
    now = datetime.utcnow()

    due_publications = Publication.query.filter(
        Publication.candidate_schedule_enabled == True,
        Publication.is_active == True,
        Publication.next_candidate_schedule_run <= now
    ).all()

    triggered_count = 0
    for publication in due_publications:
        trigger_scheduled_candidate_content_workflow.delay(publication.id)

        publication.last_candidate_schedule_run = now
        publication.next_candidate_schedule_run = calculate_next_candidate_run(publication)
        triggered_count += 1

    if triggered_count > 0:
        db.session.commit()

    return {
        'checked_at': now.isoformat(),
        'triggered_count': triggered_count
    }


@celery.task(name='app.tasks.check_research_schedules')
def check_research_schedules():
    """
    Check if any publications are due for research scanning.
    Runs every hour via Celery Beat. Dispatches research for publications
    whose last_research_run is null or older than 24 hours.
    """
    now = datetime.utcnow()
    one_day_ago = now - timedelta(hours=24)

    due_publications = Publication.query.filter(
        Publication.is_active == True,
        db.or_(
            Publication.last_research_run == None,
            Publication.last_research_run <= one_day_ago,
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


@celery.task(name='app.tasks.research_publication_sources', bind=True, max_retries=2, default_retry_delay=60, time_limit=3600, soft_time_limit=3300)
def research_publication_sources(self, publication_id):
    """
    Scan all active sources for a publication, discover candidate articles,
    deduplicate, triage via LLM, score, enrich, and store them.
    """
    from app.research.scrapers import get_scraper
    from app.research.dedup import url_hash, is_duplicate_candidate, is_already_content
    from app.research.scoring import score_candidate, compute_recency_score
    from app.research.enrichment import enrich_item, _extract_date_from_url
    from app.research.triage import triage_items, TRIAGE_MULTIPLIERS, _SKIP_TRIAGE_SOURCE_TYPES

    publication = Publication.query.get(publication_id)
    if not publication or not publication.is_active:
        return {'error': f'Publication {publication_id} not found or inactive'}

    sources = NewsSource.query.filter_by(
        publication_id=publication_id, is_active=True
    ).all()

    # Build excluded domains from Competitor, House Content sources, and the publication's own domain.
    # These are filtered out of Keyword Search results to avoid duplicating dedicated source coverage.
    excluded_domains = set()
    if publication.publication_domain:
        excluded_domains.add(publication.publication_domain.lower().strip())
    for s in sources:
        if s.source_type in ('Competitor', 'House Content') and s.url:
            domain = urlparse(s.url).netloc.lower()
            if domain:
                # Strip www. prefix for broader matching
                excluded_domains.add(domain)
                if domain.startswith('www.'):
                    excluded_domains.add(domain[4:])
                else:
                    excluded_domains.add(f'www.{domain}')
    if excluded_domains:
        logger.info(f"Keyword search exclusion domains: {excluded_domains}")

    enrichment_min_score = current_app.config.get('ENRICHMENT_MIN_SCORE', 25.0)
    enrichment_budget = current_app.config.get('ENRICHMENT_MAX_PER_RUN', 50)
    triage_enabled = current_app.config.get('TRIAGE_ENABLED', True)

    stats = {
        'sources_scanned': 0,
        'total_discovered': 0,
        'new_candidates': 0,
        'skipped_duplicates': 0,
        'skipped_excluded': 0,
        'enriched': 0,
        'enrichment_skipped': 0,
        'enrichment_failed': 0,
        'enrichment_budget_exhausted': 0,
        'triage_relevant': 0,
        'triage_maybe': 0,
        'triage_rejected': 0,
        'triage_skipped': 0,
        'errors': 0,
    }

    # ── Phase 1: Discover + Dedup ──────────────────────────────────
    # Collect all non-duplicate items across all sources before triage.
    pending_items = []  # list of (DiscoveredItem, source, url_hash) tuples
    seen_hashes = set()  # in-memory dedup within this run

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

                # In-memory dedup: skip if another source already found this URL
                if hash_val in seen_hashes:
                    stats['skipped_duplicates'] += 1
                    continue

                if is_duplicate_candidate(hash_val, publication_id):
                    stats['skipped_duplicates'] += 1
                    continue

                if is_already_content(item.url, publication_id):
                    stats['skipped_duplicates'] += 1
                    continue

                # Filter keyword search results against excluded domains
                if source.source_type == 'Keyword Search' and excluded_domains:
                    item_domain = urlparse(item.url).netloc.lower()
                    if item_domain in excluded_domains:
                        stats['skipped_excluded'] += 1
                        continue

                # Max-age filter: skip items older than 90 days
                # Check the scraper-provided date first, then URL date pattern
                item_date = item.published_date
                if not item_date:
                    item_date = _extract_date_from_url(item.url)
                if item_date:
                    if item_date.tzinfo is None:
                        age_days = (datetime.utcnow() - item_date).days
                    else:
                        from datetime import timezone
                        age_days = (datetime.now(timezone.utc) - item_date).days
                    if age_days > 90:
                        stats['skipped_excluded'] += 1
                        continue

                seen_hashes.add(hash_val)
                pending_items.append((item, source, hash_val))

            except Exception as e:
                logger.error(f"Error deduping item {item.url}: {e}")
                stats['errors'] += 1
                continue

    # ── Phase 2: LLM Triage ────────────────────────────────────────
    # Classify pending items; not_news → save as rejected, skip enrichment.
    verdict_map = {}  # url → {verdict, reasoning}

    if triage_enabled and pending_items:
        # Separate items that should be triaged from those that skip
        triage_items_list = []
        triage_source_types = []
        skip_items = []

        for item, source, hash_val in pending_items:
            if source.source_type in _SKIP_TRIAGE_SOURCE_TYPES:
                skip_items.append((item, source, hash_val))
                stats['triage_skipped'] += 1
            else:
                triage_items_list.append((item, source, hash_val))
                triage_source_types.append(source.source_type)

        if triage_items_list:
            verdicts = triage_items(
                items=[item for item, _, _ in triage_items_list],
                source_types=triage_source_types,
                industry_description=publication.industry_description or '',
                reader_personas=publication.reader_personas or '',
            )
            for v in verdicts:
                verdict_map[v['url']] = v
                if v['verdict'] == 'relevant_news':
                    stats['triage_relevant'] += 1
                elif v['verdict'] == 'maybe':
                    stats['triage_maybe'] += 1
                elif v['verdict'] == 'not_news':
                    stats['triage_rejected'] += 1

    # ── Phase 3: Score + Enrich + Save ─────────────────────────────
    for item, source, hash_val in pending_items:
        try:
            verdict_info = verdict_map.get(item.url, {})
            verdict = verdict_info.get('verdict')
            reasoning = verdict_info.get('reasoning', '')

            # Items classified as not_news → save as rejected, skip scoring/enrichment
            if verdict == 'not_news':
                reject_metadata = item.metadata or {}
                reject_metadata['triage_verdict'] = verdict
                reject_metadata['triage_reasoning'] = reasoning

                candidate = CandidateArticle(
                    publication_id=publication_id,
                    news_source_id=source.id,
                    url=item.url,
                    url_hash=hash_val,
                    title=item.title,
                    snippet=item.snippet,
                    author=item.author,
                    published_date=item.published_date,
                    relevance_score=0,
                    keyword_score=0,
                    recency_score=0,
                    source_weight=0,
                    status='rejected',
                    extra_metadata=reject_metadata,
                )
                db.session.add(candidate)
                stats['new_candidates'] += 1
                continue

            # Score candidate
            scores = score_candidate(
                title=item.title,
                snippet=item.snippet,
                published_date=item.published_date,
                source_type=source.source_type,
                industry_description=publication.industry_description or '',
                source_keywords=source.keywords or '',
            )

            # Apply triage multiplier to relevance score
            if verdict:
                multiplier = TRIAGE_MULTIPLIERS.get(verdict, 1.0)
                scores['relevance_score'] = round(scores['relevance_score'] * multiplier, 2)

            # Enrich candidates above the score threshold
            enriched_metadata = item.metadata or {}
            wants_enrichment = (
                scores['relevance_score'] >= enrichment_min_score
                and source.source_type != 'Data'
            )
            # RSS and YouTube enrichment is free; only Firecrawl costs budget
            is_free_enrichment = (
                'rss_full_content' in enriched_metadata
                or source.source_type == 'YouTube Keywords'
            )
            firecrawl_calls = stats['enriched'] + stats['enrichment_failed'] - stats.get('_free_enrichments', 0)

            if wants_enrichment and (is_free_enrichment or firecrawl_calls < enrichment_budget):
                enriched_metadata = enrich_item(item.url, enriched_metadata, source.source_type, source_url=source.url)
                if enriched_metadata.get('enrichment_failed'):
                    stats['enrichment_failed'] += 1
                else:
                    stats['enriched'] += 1
                    if is_free_enrichment:
                        stats['_free_enrichments'] = stats.get('_free_enrichments', 0) + 1
            elif wants_enrichment:
                stats['enrichment_budget_exhausted'] += 1
                stats['enrichment_skipped'] += 1
            else:
                stats['enrichment_skipped'] += 1

            # Try to fill in missing publish dates and rescore
            published_date = item.published_date
            if not published_date:
                # Check triage-provided date first (Claude inferred from URL/title/content)
                triage_date = verdict_info.get('published_date') if verdict_info else None
                if triage_date:
                    published_date = triage_date
                # Then check enrichment metadata
                if not published_date and enriched_metadata.get('extracted_published_date'):
                    try:
                        from dateutil.parser import parse as parse_date
                        published_date = parse_date(enriched_metadata['extracted_published_date'])
                    except (ValueError, TypeError):
                        pass
                # Fall back to URL pattern
                if not published_date:
                    published_date = _extract_date_from_url(item.url)

                if published_date:
                    new_recency = compute_recency_score(published_date)
                    scores['recency_score'] = round(new_recency, 2)
                    base_relevance = round(
                        (scores['keyword_score'] * 0.50) + (new_recency * 0.30) + (scores['source_weight'] * 100 * 0.20),
                        2,
                    )
                    # Re-apply triage multiplier after rescoring
                    if verdict:
                        multiplier = TRIAGE_MULTIPLIERS.get(verdict, 1.0)
                        base_relevance = round(base_relevance * multiplier, 2)
                    scores['relevance_score'] = base_relevance

            # Add triage info to metadata
            if verdict:
                enriched_metadata['triage_verdict'] = verdict
                enriched_metadata['triage_reasoning'] = reasoning

            candidate = CandidateArticle(
                publication_id=publication_id,
                news_source_id=source.id,
                url=item.url,
                url_hash=hash_val,
                title=item.title,
                snippet=item.snippet,
                author=item.author,
                published_date=published_date,
                relevance_score=scores['relevance_score'],
                keyword_score=scores['keyword_score'],
                recency_score=scores['recency_score'],
                source_weight=scores['source_weight'],
                status='new',
                extra_metadata=enriched_metadata,
            )
            db.session.add(candidate)
            stats['new_candidates'] += 1

        except Exception as e:
            logger.error(f"Error processing item {item.url}: {e}")
            stats['errors'] += 1
            continue

    # Commit all new candidates and update last_research_run.
    # Use flush-first approach so IntegrityErrors (e.g. duplicate url_hash
    # from two sources finding the same URL) don't kill the whole batch.
    try:
        publication.last_research_run = datetime.utcnow()
        db.session.flush()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Batch commit failed for pub {publication_id}, retrying one-by-one: {e}")

        # Re-save candidates individually, skipping duplicates
        publication = Publication.query.get(publication_id)
        publication.last_research_run = datetime.utcnow()
        saved = 0
        for item, source, hash_val in pending_items:
            if is_duplicate_candidate(hash_val, publication_id):
                continue
            try:
                # Re-check for triage verdict
                verdict_info = verdict_map.get(item.url, {})
                verdict = verdict_info.get('verdict')
                if verdict == 'not_news':
                    meta = item.metadata or {}
                    meta['triage_verdict'] = verdict
                    meta['triage_reasoning'] = verdict_info.get('reasoning', '')
                    candidate = CandidateArticle(
                        publication_id=publication_id,
                        news_source_id=source.id,
                        url=item.url,
                        url_hash=hash_val,
                        title=item.title,
                        snippet=item.snippet,
                        status='rejected',
                        extra_metadata=meta,
                    )
                else:
                    candidate = CandidateArticle(
                        publication_id=publication_id,
                        news_source_id=source.id,
                        url=item.url,
                        url_hash=hash_val,
                        title=item.title,
                        snippet=item.snippet,
                        status='new',
                    )
                db.session.add(candidate)
                db.session.flush()
                saved += 1
            except Exception:
                db.session.rollback()
                publication = Publication.query.get(publication_id)
                publication.last_research_run = datetime.utcnow()
                continue
        try:
            db.session.commit()
            logger.info(f"One-by-one fallback saved {saved} candidates for pub {publication_id}")
        except Exception as e2:
            db.session.rollback()
            logger.error(f"Fallback commit also failed for pub {publication_id}: {e2}")
            raise self.retry(exc=e2)

    stats.pop('_free_enrichments', None)
    logger.info(f"Research complete for publication {publication_id}: {stats}")
    return stats
