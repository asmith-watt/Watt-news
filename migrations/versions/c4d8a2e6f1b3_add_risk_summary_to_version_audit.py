"""Add risk_summary to version_audit

Revision ID: c4d8a2e6f1b3
Revises: aa5036c056b1
Create Date: 2026-09-23 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d8a2e6f1b3'
down_revision = 'aa5036c056b1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('version_audit', schema=None) as batch_op:
        batch_op.add_column(sa.Column('risk_summary', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('version_audit', schema=None) as batch_op:
        batch_op.drop_column('risk_summary')
