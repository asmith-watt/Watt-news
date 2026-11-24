"""Add research_run table for live research job status

Revision ID: d2b6f8a4c1e7
Revises: b7c3e91d4f20
Create Date: 2026-11-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2b6f8a4c1e7'
down_revision = 'b7c3e91d4f20'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'research_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('publication_id', sa.Integer(), nullable=False),
        sa.Column('trigger', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('phase', sa.String(length=32), nullable=True),
        sa.Column('message', sa.String(length=512), nullable=True),
        sa.Column('progress_done', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('progress_total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stats', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['publication_id'], ['publication.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('research_run', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_run_publication_id'), ['publication_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_research_run_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_research_run_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('research_run', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_research_run_created_at'))
        batch_op.drop_index(batch_op.f('ix_research_run_status'))
        batch_op.drop_index(batch_op.f('ix_research_run_publication_id'))
    op.drop_table('research_run')
