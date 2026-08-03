import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from accounts.models import AccountModel, AccountType
from imports.models import (
    ImportBatchModel,
    ImportFileFormat,
    ImportResolutionModel,
    ImportResolutionSource,
    ImportResolutionType,
    ImportRowModel,
    ImportRowStatus,
    ImportStatus,
)
from portfolios.models import PortfolioModel
from shared.model_registry import load_domain_models

load_domain_models()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_import_staging_round_trip_and_database_invariants() -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    portfolio_id = uuid4()
    account_id = uuid4()
    batch_id = uuid4()
    row_id = uuid4()
    sha256 = uuid4().hex * 2

    try:
        async with sessions.begin() as session:
            session.add(
                PortfolioModel(
                    id=portfolio_id,
                    name="Synthetic import portfolio",
                    base_currency="USD",
                )
            )
            await session.flush()
            session.add(
                AccountModel(
                    id=account_id,
                    portfolio_id=portfolio_id,
                    name="Synthetic import account",
                    account_type=AccountType.BROKER,
                )
            )
            await session.flush()
            session.add(
                ImportBatchModel(
                    id=batch_id,
                    portfolio_id=portfolio_id,
                    account_id=account_id,
                    source_provider="universal_broker",
                    declared_format=ImportFileFormat.CSV,
                    original_filename="synthetic.csv",
                    storage_key=f"imports/{batch_id}",
                    file_size_bytes=128,
                    sha256=sha256,
                )
            )
            await session.flush()
            session.add(
                ImportRowModel(
                    id=row_id,
                    batch_id=batch_id,
                    sequence_number=1,
                    source_row_number=2,
                    raw_data={"Date": "2026-08-03", "Type": "Buy"},
                    normalized_candidate={"operation_type": "trade"},
                    status=ImportRowStatus.WARNING,
                    warnings=[{"code": "instrument_match_required"}],
                )
            )
            await session.flush()
            session.add(
                ImportResolutionModel(
                    batch_id=batch_id,
                    row_id=row_id,
                    resolution_type=ImportResolutionType.INSTRUMENT_MATCH,
                    source=ImportResolutionSource.USER,
                    payload={"instrument_id": str(uuid4())},
                )
            )

        async with sessions() as session:
            stored_batch = await session.scalar(
                select(ImportBatchModel).where(ImportBatchModel.id == batch_id)
            )
            stored_row = await session.scalar(
                select(ImportRowModel).where(ImportRowModel.id == row_id)
            )
            stored_resolution = await session.scalar(
                select(ImportResolutionModel).where(ImportResolutionModel.row_id == row_id)
            )

            assert stored_batch is not None
            assert stored_batch.status is ImportStatus.UPLOADED
            assert stored_batch.sha256 == sha256
            assert stored_row is not None
            assert stored_row.raw_data == {"Date": "2026-08-03", "Type": "Buy"}
            assert stored_row.normalized_candidate == {"operation_type": "trade"}
            assert stored_resolution is not None
            assert stored_resolution.batch_id == batch_id

        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(
                    ImportBatchModel(
                        portfolio_id=uuid4(),
                        account_id=account_id,
                        source_provider="universal_broker",
                        declared_format=ImportFileFormat.CSV,
                        original_filename="mismatched.csv",
                        storage_key=f"imports/{uuid4()}",
                        file_size_bytes=64,
                        sha256=uuid4().hex * 2,
                    )
                )

        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(
                    ImportBatchModel(
                        portfolio_id=portfolio_id,
                        account_id=account_id,
                        source_provider="universal_broker",
                        declared_format=ImportFileFormat.CSV,
                        original_filename="duplicate.csv",
                        storage_key=f"imports/{uuid4()}",
                        file_size_bytes=128,
                        sha256=sha256,
                    )
                )
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(ImportBatchModel).where(ImportBatchModel.id == batch_id))
            await session.execute(delete(PortfolioModel).where(PortfolioModel.id == portfolio_id))
        await engine.dispose()
