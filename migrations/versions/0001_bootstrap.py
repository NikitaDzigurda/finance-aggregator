"""Create the initial schema baseline.

Revision ID: 0001_bootstrap
Revises:
Create Date: 2026-08-03
"""

from collections.abc import Sequence

revision: str = "0001_bootstrap"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish an explicit baseline before domain tables are introduced."""


def downgrade() -> None:
    """Remove the empty baseline."""
