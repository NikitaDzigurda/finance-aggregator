from collections.abc import Sequence

from alembic import op

revision: str = "0009_xml_operation_source"
down_revision: str | Sequence[str] | None = "0008_xml_import_format"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("operation_source_type", "operations", type_="check")
    op.create_check_constraint(
        "operation_source_type",
        "operations",
        "source_type IN ('manual', 'csv_import', 'xlsx_import', 'xml_import', 'pdf_import')",
    )


def downgrade() -> None:
    op.drop_constraint("operation_source_type", "operations", type_="check")
    op.create_check_constraint(
        "operation_source_type",
        "operations",
        "source_type IN ('manual', 'csv_import', 'xlsx_import', 'pdf_import')",
    )
