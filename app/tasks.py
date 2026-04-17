"""Celery tasks for scheduled content generation and research."""
import logging
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse
import requests
from flask import current_app

from app.celery import celery
from app import db
from app.models import Publication, WorkflowRun, CandidateArticle, NewsSource, ResearchLog, WeeklyBriefing, AuthorProfile

logger = logging.getLogger(__name__)


def _notify_safe(publication, job_type, stats, errors=None):
    """Fire-and-forget notification wrapper — never raises."""
    try:
        from app.notifications import send_job_notification
        send_job_notification(publication, job_type, stats, errors)
    except Exception as e:
        logger.error(f'Notification failed for {job_type} on pub {publication.id}: {e}')


def _research_log(publication_id, phase, level, message, source_id=None, url=None, details=None):
    """Write a structured log entry to the research_log table. Never raises."""
    try:
        entry = ResearchLog(
            publication_id=publication_id,
            news_source_id=source_id,
            phase=phase,
            level=level,
            message=message,
            url=url,
            details=details,
        )
        db.session.add(entry)
        db.session.flush()
    except Exception as e:
        logger.warning(f"Failed to write research log: {e}")


@celery.task(name='app.tasks.trigger_scheduled_content_workflow')
def trigger_scheduled_content_workflow(publication_id):
    """
    Trigger the candidate content generation workflow for a publication.
    Called by the scheduler. Uses the same n8n candidate workflow as the
    dashboard "Generate Content from Candidates" button.
    """
    publication = Publication.query.get(publication_id)
    if not publication:
        return {'error': f'Publication {publication_id} not found'}

    if not publication.is_active:
        return {'error': f'Publication {publication_id} is not active'}

    workflow_url = current_app.config.get('N8N_CANDIDATE_CONTENT_WORKFLOW_URL')
    if not workflow_url:
        return {'error': 'N8N_CANDIDATE_CONTENT_WORKFLOW_URL not configured'}

    # Create workflow run record (triggered_by_id=None for system-triggered)
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=publication.id,
        triggered_by_id=None,  # System-triggered (scheduled)
        workflow_type='candidate_content_generation',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    # Include default author style guide if available
    payload = {
        'publication_id': publication.id,
        'workflow_id': workflow_id,
    }
    default_author = AuthorProfile.query.filter_by(
        publication_id=publication.id, is_default=True, is_active=True
    ).first()
    if default_author and default_author.style_guide:
        payload['author_name'] = default_author.name
        payload['author_style_guide'] = default_author.style_guide

    try:
        requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
        workflow_run.status = 'running'
        db.session.commit()

        result = {
            'success': True,
            'workflow_id': workflow_id,
            'publication_id': publication_id
        }
        _notify_safe(publication, 'candidate_content_generation', result)
        return result

    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        result = {'error': str(e), 'workflow_id': workflow_id}
        _notify_safe(publication, 'candidate_content_generation', result)
        return result


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
    """Trigger the candidate research pipeline for a publication on schedule."""
    publication = Publication.query.get(publication_id)
    if not publication:
        return {'error': f'Publication {publication_id} not found'}

    if not publication.is_active:
        return {'error': f'Publication {publication_id} is not active'}

    has_sources = NewsSource.query.filter_by(
        publication_id=publication_id, is_active=True
    ).first() is not None
    if not has_sources:
        return {'error': f'Publication {publication_id} has no active sources'}

    research_publication_sources.delay(publication_id)
    return {'success': True, 'publication_id': publication_id}


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
    from app.research.dedup import url_hash, is_duplicate_candidate, is_already_content, sanitize_url
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
            _research_log(publication_id, 'discovery', 'error',
                          f"Scraper failed: {e}", source_id=source.id,
                          details={'source_type': source.source_type, 'exception': str(e)})
            stats['errors'] += 1
            continue

        for item in items:
            try:
                item.url = sanitize_url(item.url)
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
                _research_log(publication_id, 'dedup', 'error',
                              f"Dedup failed: {e}", source_id=source.id, url=item.url)
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
                    _research_log(publication_id, 'enrichment', 'warning',
                                  f"Enrichment failed", source_id=source.id, url=item.url,
                                  details={'reason': enriched_metadata.get('enrichment_error')})
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
            _research_log(publication_id, 'scoring', 'error',
                          f"Processing failed: {e}", source_id=source.id, url=item.url,
                          details={'exception': str(e)})
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
        _research_log(publication_id, 'save', 'error',
                      f"Batch commit failed, falling back to one-by-one: {e}")

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
            _research_log(publication_id, 'save', 'error',
                          f"Fallback commit also failed: {e2}")
            raise self.retry(exc=e2)

    stats.pop('_free_enrichments', None)
    logger.info(f"Research complete for publication {publication_id}: {stats}")
    _research_log(publication_id, 'discovery', 'info',
                  f"Research run complete: {stats['new_candidates']} new, "
                  f"{stats['errors']} errors, {stats['sources_scanned']} sources scanned",
                  details=stats)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    _notify_safe(publication, 'research', stats)
    return stats


@celery.task(name='app.tasks.retriage_source_candidates')
def retriage_source_candidates(source_id):
    """Re-run LLM triage on rejected candidates for a source using current publication settings."""
    from app.research.scrapers import DiscoveredItem
    from app.research.triage import triage_items, TRIAGE_MULTIPLIERS
    from app.research.scoring import score_candidate
    from app.research.enrichment import enrich_item

    source = NewsSource.query.get(source_id)
    if not source:
        logger.error(f"Re-triage: source {source_id} not found")
        return

    publication = Publication.query.get(source.publication_id)
    if not publication:
        logger.error(f"Re-triage: publication for source {source_id} not found")
        return

    rejected = CandidateArticle.query.filter_by(
        news_source_id=source_id,
        status='rejected',
    ).all()

    if not rejected:
        logger.info(f"Re-triage: no rejected candidates for source {source_id}")
        return {'retriage_total': 0, 'promoted': 0, 'still_rejected': 0}

    # Reconstruct DiscoveredItems from rejected candidates
    items = []
    source_types = []
    for candidate in rejected:
        items.append(DiscoveredItem(
            url=candidate.url,
            title=candidate.title,
            snippet=candidate.snippet,
            author=candidate.author,
            published_date=candidate.published_date,
            metadata=candidate.extra_metadata or {},
        ))
        source_types.append(source.source_type or '')

    # Re-triage with current publication settings
    verdicts = triage_items(
        items=items,
        source_types=source_types,
        industry_description=publication.industry_description or '',
        reader_personas=publication.reader_personas or '',
    )

    verdict_map = {v['url']: v for v in verdicts}

    promoted = 0
    still_rejected = 0
    enrichment_budget = current_app.config.get('ENRICHMENT_MAX_PER_RUN', 50)
    enrichment_min_score = current_app.config.get('ENRICHMENT_MIN_SCORE', 25.0)
    enrichments_used = 0

    for candidate in rejected:
        verdict_info = verdict_map.get(candidate.url, {})
        new_verdict = verdict_info.get('verdict', 'not_news')
        reasoning = verdict_info.get('reasoning', '')

        meta = candidate.extra_metadata or {}
        meta['retriage_verdict'] = new_verdict
        meta['retriage_reasoning'] = reasoning
        meta['retriage_previous_verdict'] = meta.get('triage_verdict', 'not_news')

        if new_verdict in ('relevant_news', 'maybe'):
            # Re-score the candidate
            scores = score_candidate(
                title=candidate.title,
                snippet=candidate.snippet,
                published_date=candidate.published_date,
                source_type=source.source_type or '',
                industry_description=publication.industry_description or '',
                source_keywords=source.keywords or '',
            )

            multiplier = TRIAGE_MULTIPLIERS.get(new_verdict, 1.0)
            scores['relevance_score'] = round(scores['relevance_score'] * multiplier, 2)

            # Enrich if above threshold and budget allows
            if scores['relevance_score'] >= enrichment_min_score and enrichments_used < enrichment_budget:
                try:
                    item = DiscoveredItem(
                        url=candidate.url,
                        title=candidate.title,
                        snippet=candidate.snippet,
                        metadata=meta,
                    )
                    enriched_meta, is_free = enrich_item(item, source.source_type or '')
                    meta.update(enriched_meta)
                    if not is_free:
                        enrichments_used += 1
                except Exception as e:
                    logger.warning(f"Re-triage enrichment failed for {candidate.url}: {e}")

            candidate.status = 'new'
            candidate.relevance_score = scores['relevance_score']
            candidate.keyword_score = scores['keyword_score']
            candidate.recency_score = scores['recency_score']
            candidate.source_weight = scores['source_weight']
            meta['triage_verdict'] = new_verdict
            meta['triage_reasoning'] = reasoning
            promoted += 1
        else:
            still_rejected += 1

        candidate.extra_metadata = meta

    db.session.commit()
    stats = {
        'retriage_total': len(rejected),
        'promoted': promoted,
        'still_rejected': still_rejected,
    }
    logger.info(f"Re-triage complete for source {source_id}: {stats}")
    return stats


@celery.task(name='app.tasks.generate_weekly_briefings')
def generate_weekly_briefings(publication_id=None):
    """Generate weekly briefing summaries from recent candidate articles.

    If publication_id is provided, generates for that publication only.
    Otherwise generates for all active publications.
    """
    import anthropic
    import json

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured, skipping weekly briefings")
        return {'error': 'no API key'}

    model = current_app.config.get('BRIEFING_MODEL', 'claude-haiku-4-5-20251001')
    min_score = current_app.config.get('BRIEFING_MIN_SCORE', 30.0)

    if publication_id:
        publications = Publication.query.filter_by(id=publication_id, is_active=True).all()
    else:
        publications = Publication.query.filter_by(is_active=True).all()

    period_end = datetime.utcnow().date()
    period_start = period_end - timedelta(days=7)

    generated = 0
    for publication in publications:
        candidates = CandidateArticle.query.filter(
            CandidateArticle.publication_id == publication.id,
            CandidateArticle.status != 'rejected',
            CandidateArticle.relevance_score >= min_score,
            CandidateArticle.discovered_at >= datetime.combine(period_start, datetime.min.time()),
        ).order_by(CandidateArticle.relevance_score.desc()).limit(50).all()

        if not candidates:
            logger.info(f"Weekly briefing: no qualifying candidates for pub {publication.id}")
            continue

        # Build candidate summary for the prompt
        candidate_lines = []
        for c in candidates:
            meta = c.extra_metadata or {}
            verdict = meta.get('triage_verdict', 'unknown')
            line = f"- [{c.relevance_score:.0f}] {c.title or 'Untitled'}"
            if c.snippet:
                line += f" — {c.snippet[:200]}"
            line += f" (triage: {verdict})"
            candidate_lines.append(line)

        system_prompt = (
            "You are an industry analyst writing a concise weekly briefing for editors "
            "of a trade publication. Summarize the key themes, trends, and notable stories "
            "from the candidate articles below into 1-2 paragraphs. Be specific about what "
            "happened and why it matters to the readers. Do not list articles individually — "
            "synthesize them into a narrative.\n\n"
            f"## Publication Industry\n{publication.industry_description or 'Not specified'}\n\n"
            f"## Reader Personas\n{publication.reader_personas or 'Not specified'}\n\n"
            f"## Period\n{period_start.strftime('%B %d')} – {period_end.strftime('%B %d, %Y')}"
        )

        user_message = (
            f"Here are {len(candidates)} candidate articles discovered this week, "
            f"ordered by relevance score (higher = more relevant):\n\n"
            + "\n".join(candidate_lines)
        )

        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{'role': 'user', 'content': user_message}],
            )

            summary = ''
            for block in response.content:
                if hasattr(block, 'text'):
                    summary += block.text

            if not summary.strip():
                logger.warning(f"Weekly briefing: empty response for pub {publication.id}")
                continue

            briefing = WeeklyBriefing(
                publication_id=publication.id,
                summary=summary.strip(),
                period_start=period_start,
                period_end=period_end,
                candidate_count=len(candidates),
            )
            db.session.add(briefing)
            db.session.commit()
            generated += 1
            logger.info(f"Weekly briefing generated for pub {publication.id}: {len(candidates)} candidates")

        except Exception as e:
            logger.error(f"Weekly briefing failed for pub {publication.id}: {e}")
            continue

    return {'generated': generated}


@celery.task(name='app.tasks.generate_author_style_guide')
def generate_author_style_guide(author_profile_id):
    """Analyze sample articles and generate a writing style guide for an author profile."""
    import anthropic

    profile = AuthorProfile.query.get(author_profile_id)
    if not profile:
        return {'error': f'AuthorProfile {author_profile_id} not found'}

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured, skipping style guide generation")
        return {'error': 'no API key'}

    samples = profile.sample_articles or []
    if not samples:
        return {'error': 'no sample articles provided'}

    # Fetch content for any URLs in the samples
    article_texts = []
    for sample in samples:
        if isinstance(sample, str) and sample.startswith('http'):
            try:
                from app.research.enrichment import _firecrawl_scrape
                firecrawl_key = current_app.config.get('FIRECRAWL_API_KEY')
                if firecrawl_key:
                    data = _firecrawl_scrape(sample, firecrawl_key)
                    if data and data.get('markdown'):
                        article_texts.append(data['markdown'][:5000])
                        continue
            except Exception as e:
                logger.warning(f"Failed to fetch sample URL {sample}: {e}")
            article_texts.append(f"[Could not fetch: {sample}]")
        elif isinstance(sample, str):
            article_texts.append(sample[:5000])

    if not article_texts:
        return {'error': 'no usable sample content'}

    system_prompt = (
        "You are a writing style analyst. Analyze the provided sample articles and produce a "
        "concise writing style guide that captures this author's voice. The guide will be used "
        "to instruct an AI to write in this author's style.\n\n"
        "Cover these aspects:\n"
        "- **Tone & Voice**: Formal/informal, authoritative/conversational, serious/witty\n"
        "- **Sentence Structure**: Short/long, simple/complex, varied/consistent\n"
        "- **Vocabulary**: Technical level, jargon usage, word preferences\n"
        "- **Opening Style**: How they typically begin articles\n"
        "- **Paragraph Structure**: Length, transitions, use of subheadings\n"
        "- **Attribution & Evidence**: How they cite sources, use quotes, reference data\n"
        "- **Distinctive Patterns**: Rhetorical questions, analogies, humor, direct address\n\n"
        "Output ONLY the style guide, written as direct instructions (e.g. 'Use short, punchy sentences. "
        "Open with a concrete example or surprising fact.'). Keep it under 500 words."
    )

    numbered_samples = []
    for i, text in enumerate(article_texts, 1):
        numbered_samples.append(f"--- SAMPLE {i} ---\n{text}")
    user_message = "\n\n".join(numbered_samples)

    try:
        model = current_app.config.get('STYLE_GUIDE_MODEL', 'claude-haiku-4-5-20251001')
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_message}],
        )

        guide = ''
        for block in response.content:
            if hasattr(block, 'text'):
                guide += block.text

        if guide.strip():
            profile.style_guide = guide.strip()
            db.session.commit()
            logger.info(f"Style guide generated for author profile {author_profile_id}")
            return {'success': True}
        else:
            return {'error': 'empty response from LLM'}

    except Exception as e:
        logger.error(f"Style guide generation failed for profile {author_profile_id}: {e}")
        return {'error': str(e)}
