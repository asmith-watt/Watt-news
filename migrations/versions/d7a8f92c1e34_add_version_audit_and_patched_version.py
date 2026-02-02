"""Add VersionAudit and PatchedVersion tables

Revision ID: d7a8f92c1e34
Revises: c3a6aee02773
Create Date: 2026-02-02 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7a8f92c1e34'
down_revision = 'c3a6aee02773'
branch_labels = None
depends_on = None


def upgrade():
    # Create version_audit table
    op.create_table('version_audit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workflow_run_id', sa.String(length=36), nullable=True),
        sa.Column('content_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('ai_provider', sa.String(length=32), nullable=True),
        sa.Column('ai_model', sa.String(length=64), nullable=True),
        sa.Column('overall_risk', sa.String(length=32), nullable=True),
        sa.Column('original_draft', sa.Text(), nullable=True),
        sa.Column('issues', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['content_id'], ['news_content.id'], ),
        sa.ForeignKeyConstraint(['version_id'], ['content_version.id'], ),
        sa.ForeignKeyConstraint(['workflow_run_id'], ['workflow_run.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('version_audit', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_version_audit_content_id'), ['content_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_version_audit_version_id'), ['version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_version_audit_workflow_run_id'), ['workflow_run_id'], unique=False)

    # Create patched_version table
    op.create_table('patched_version',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workflow_run_id', sa.String(length=36), nullable=True),
        sa.Column('content_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('ai_provider', sa.String(length=32), nullable=True),
        sa.Column('ai_model', sa.String(length=64), nullable=True),
        sa.Column('patched_draft', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['content_id'], ['news_content.id'], ),
        sa.ForeignKeyConstraint(['version_id'], ['content_version.id'], ),
        sa.ForeignKeyConstraint(['workflow_run_id'], ['workflow_run.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patched_version', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patched_version_content_id'), ['content_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_patched_version_version_id'), ['version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_patched_version_workflow_run_id'), ['workflow_run_id'], unique=False)


def downgrade():
    with op.batch_alter_table('patched_version', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patched_version_workflow_run_id'))
        batch_op.drop_index(batch_op.f('ix_patched_version_version_id'))
        batch_op.drop_index(batch_op.f('ix_patched_version_content_id'))
    op.drop_table('patched_version')

    with op.batch_alter_table('version_audit', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_version_audit_workflow_run_id'))
        batch_op.drop_index(batch_op.f('ix_version_audit_version_id'))
        batch_op.drop_index(batch_op.f('ix_version_audit_content_id'))
    op.drop_table('version_audit')
