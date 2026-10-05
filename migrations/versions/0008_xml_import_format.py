from collections.abc import Sequence

from alembic import op

revision: str = "0008_xml_import_format"
down_revision: str | Sequence[str] | None = "0007_portfolio_calculation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("import_file_format", "import_batches", type_="check")
    op.create_check_constraint(
        "import_file_format",
        "import_batches",
        "declared_format IN ('csv', 'xlsx', 'xml', 'pdf')",
    )


def downgrade() -> None:
    op.drop_constraint("import_file_format", "import_batches", type_="check")
    op.create_check_constraint(
        "import_file_format",
        "import_batches",
        "declared_format IN ('csv', 'xlsx', 'pdf')",
    )
