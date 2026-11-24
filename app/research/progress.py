"""Live progress reporting for research runs.

The research task keeps every new candidate in its session until one batch
commit at the end, so progress can't be committed through that session
without saving half a run. Instead each update is its own short transaction
on a separate connection.
"""
import logging
import time
from datetime import datetime

from app import db
from app.models import ResearchRun

logger = logging.getLogger(__name__)

# Minimum seconds between non-forced writes; the UI polls every few seconds.
_MIN_INTERVAL = 2.0


class RunProgress:
    """Throttled writer for one ResearchRun row. Never raises."""

    def __init__(self, run_id):
        self.run_id = run_id
        self._last_write = 0.0
        self._pending = {}

    def update(self, force=False, **fields):
        if not self.run_id:
            return
        self._pending.update(fields)
        now = time.monotonic()
        if not force and now - self._last_write < _MIN_INTERVAL:
            return
        self._flush(now)

    def _flush(self, now):
        values = dict(self._pending)
        values['updated_at'] = datetime.utcnow()
        if 'message' in values and values['message']:
            values['message'] = values['message'][:512]
        try:
            table = ResearchRun.__table__
            with db.engine.begin() as conn:
                conn.execute(table.update().where(table.c.id == self.run_id).values(**values))
            self._pending = {}
            self._last_write = now
        except Exception as e:
            logger.warning(f"Failed to write progress for research run {self.run_id}: {e}")

    def start(self):
        self.update(force=True, status='running', started_at=datetime.utcnow(),
                    phase='starting', message='Starting research', error=None)

    def phase(self, phase, message, total=0):
        self.update(force=True, phase=phase, message=message,
                    progress_done=0, progress_total=total)

    def step(self, done, message=None):
        fields = {'progress_done': done}
        if message:
            fields['message'] = message
        self.update(**fields)

    def complete(self, stats):
        self.update(force=True, status='completed', phase='done', finished_at=datetime.utcnow(),
                    message=f"Found {stats.get('new_candidates', 0)} new candidates",
                    stats=stats)

    def fail(self, error, stats=None):
        fields = dict(status='failed', finished_at=datetime.utcnow(), error=str(error)[:4000],
                      message='Research failed')
        if stats is not None:
            fields['stats'] = stats
        self.update(force=True, **fields)

    def requeue(self, reason):
        self.update(force=True, status='queued', phase='retrying',
                    message=f'Retrying after an error: {reason}'[:512])


def latest_run(publication_id):
    return (ResearchRun.query
            .filter_by(publication_id=publication_id)
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
            .first())


def active_run(publication_id):
    """The publication's queued/running run, ignoring ones that went stale."""
    runs = (ResearchRun.query
            .filter(ResearchRun.publication_id == publication_id,
                    ResearchRun.status.in_(ResearchRun.ACTIVE_STATUSES))
            .order_by(ResearchRun.created_at.desc())
            .all())
    return next((r for r in runs if r.is_active), None)
