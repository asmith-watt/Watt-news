"""Email notifications for scheduled job completions via Mailgun."""
import logging
import re
from datetime import datetime

import requests
from flask import current_app

logger = logging.getLogger(__name__)


def send_job_notification(publication, job_type, stats, errors=None):
    """Send a summary email when a scheduled job completes.

    Args:
        publication: Publication model instance.
        job_type: One of 'research', 'content_generation', 'candidate_content_generation'.
        stats: Dict of key metrics (varies by job type).
        errors: Optional list of error strings.
    """
    # Parse recipients from publication
    raw = getattr(publication, 'notification_emails', None) or ''
    recipients = [e.strip() for e in re.split(r'[,\s]+', raw) if e.strip()]
    if not recipients:
        return

    api_key = current_app.config.get('MAILGUN_API_KEY')
    domain = current_app.config.get('MAILGUN_DOMAIN')
    from_addr = current_app.config.get('MAILGUN_FROM', 'WATT Automation <noreply@mg.example.com>')

    if not api_key or not domain:
        logger.warning('Mailgun not configured (MAILGUN_API_KEY / MAILGUN_DOMAIN missing) — skipping notification')
        return

    subject = _build_subject(publication, job_type, stats)
    html = _build_html(publication, job_type, stats, errors)

    try:
        resp = requests.post(
            f'https://api.mailgun.net/v3/{domain}/messages',
            auth=('api', api_key),
            data={
                'from': from_addr,
                'to': recipients,
                'subject': subject,
                'html': html,
            },
            timeout=10,
        )
        if resp.ok:
            logger.info(f'Notification sent for {job_type} on "{publication.name}" to {recipients}')
        else:
            logger.error(f'Mailgun returned {resp.status_code}: {resp.text}')
    except Exception as e:
        logger.error(f'Failed to send notification email: {e}')


def _build_subject(publication, job_type, stats):
    """Build a concise email subject line."""
    pub_name = publication.name
    if job_type == 'research':
        new = stats.get('new_candidates', 0)
        return f'[WATT] Research complete — {pub_name} — {new} new candidates'
    elif job_type == 'content_generation':
        if stats.get('error'):
            return f'[WATT] Content generation FAILED — {pub_name}'
        return f'[WATT] Content generation triggered — {pub_name}'
    elif job_type == 'candidate_content_generation':
        if stats.get('error'):
            return f'[WATT] Candidate content generation FAILED — {pub_name}'
        return f'[WATT] Candidate content generation triggered — {pub_name}'
    return f'[WATT] Job complete — {pub_name}'


def _build_html(publication, job_type, stats, errors):
    """Build an inline-styled HTML email body."""
    job_labels = {
        'research': 'Research Scan',
        'content_generation': 'Content Generation',
        'candidate_content_generation': 'Candidate Content Generation',
    }
    job_label = job_labels.get(job_type, job_type)
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    # Build stats rows
    stats_html = ''
    if stats:
        rows = ''
        for key, value in stats.items():
            if key.startswith('_'):
                continue
            label = key.replace('_', ' ').title()
            rows += (
                f'<tr>'
                f'<td style="padding:6px 12px;border-bottom:1px solid #eee;color:#555;">{label}</td>'
                f'<td style="padding:6px 12px;border-bottom:1px solid #eee;font-weight:bold;">{value}</td>'
                f'</tr>'
            )
        stats_html = (
            f'<table style="border-collapse:collapse;width:100%;margin:16px 0;">'
            f'{rows}'
            f'</table>'
        )

    # Build errors section
    errors_html = ''
    error_list = errors or []
    if stats.get('error'):
        error_list = [stats['error']] + list(error_list)
    if stats.get('errors') and isinstance(stats['errors'], int) and stats['errors'] > 0:
        error_list.append(f'{stats["errors"]} processing error(s) during run — see logs for details')
    if error_list:
        items = ''.join(f'<li style="margin-bottom:4px;">{e}</li>' for e in error_list)
        errors_html = (
            f'<div style="background:#fff0f0;border:1px solid #e55;border-radius:6px;padding:12px;margin:16px 0;">'
            f'<strong style="color:#c00;">Errors</strong>'
            f'<ul style="margin:8px 0 0 0;padding-left:20px;color:#900;">{items}</ul>'
            f'</div>'
        )

    return (
        f'<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">'
        f'<h2 style="color:#1a2b3c;margin:0 0 4px 0;">{publication.name}</h2>'
        f'<h3 style="color:#666;margin:0 0 16px 0;font-weight:normal;">{job_label}</h3>'
        f'{stats_html}'
        f'{errors_html}'
        f'<p style="color:#999;font-size:12px;margin-top:24px;">Sent at {timestamp}</p>'
        f'</div>'
    )
