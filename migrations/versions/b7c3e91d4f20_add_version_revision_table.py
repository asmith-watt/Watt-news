"""Add version_revision table

Revision ID: b7c3e91d4f20
Revises: c4d8a2e6f1b3
Create Date: 2026-09-23 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c3e91d4f20'
down_revision = 'c4d8a2e6f1b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('version_revision',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('content_id', sa.Integer(), nullable=False),
    sa.Column('version_id', sa.Integer(), nullable=False),
    sa.Column('requested_by_id', sa.Integer(), nullable=True),
    sa.Column('instructions', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('ai_model', sa.String(length=64), nullable=True),
    sa.Column('original', sa.JSON(), nullable=True),
    sa.Column('revised', sa.JSON(), nullable=True),
    sa.Column('change_summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('applied_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['content_id'], ['news_content.id'], ),
    sa.ForeignKeyConstraint(['version_id'], ['content_version.id'], ),
    sa.ForeignKeyConstraint(['requested_by_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('version_revision', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_version_revision_content_id'), ['content_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_version_revision_version_id'), ['version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_version_revision_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('version_revision', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_version_revision_status'))
        batch_op.drop_index(batch_op.f('ix_version_revision_version_id'))
        batch_op.drop_index(batch_op.f('ix_version_revision_content_id'))

    op.drop_table('version_revision')
