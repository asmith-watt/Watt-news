"""Celery tasks for scheduled content generation."""
import uuid
from datetime import datetime, timedelta
import requests
from flask import current_app

from app.celery import celery
from app import db
from app.models import Publication, WorkflowRun


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
