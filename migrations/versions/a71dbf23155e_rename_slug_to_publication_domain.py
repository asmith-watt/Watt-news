"""Rename slug to publication_domain

Revision ID: a71dbf23155e
Revises: 77167ff5edba
Create Date: 2025-12-15 08:34:56.106977

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a71dbf23155e'
down_revision = '77167ff5edba'
branch_labels = None
depends_on = None


def upgrade():
    # Rename the column from slug to publication_domain
    with op.batch_alter_table('publication', schema=None) as batch_op:
        batch_op.drop_index('ix_publication_slug')

    op.alter_column('publication', 'slug', new_column_name='publication_domain')

    with op.batch_alter_table('publication', schema=None) as batch_op:
        batch_op.create_index('ix_publication_publication_domain', ['publication_domain'], unique=True)


def downgrade():
    # Rename the column back from publication_domain to slug
    with op.batch_alter_table('publication', schema=None) as batch_op:
        batch_op.drop_index('ix_publication_publication_domain')

    op.alter_column('publication', 'publication_domain', new_column_name='slug')

    with op.batch_alter_table('publication', schema=None) as batch_op:
        batch_op.create_index('ix_publication_slug', ['slug'], unique=True)