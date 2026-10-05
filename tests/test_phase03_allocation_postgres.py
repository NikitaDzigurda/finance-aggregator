import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from allocation.models import AllocationCategoryModel, InstrumentCategoryOverrideModel
from apps.api.main import create_app
from instruments.models import InstrumentModel
from portfolios.models import PortfolioModel
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_allocation_category_override_is_scoped_and_used_category_is_protected() -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    suffix = uuid4().hex[:8].upper()
    portfolio_ids: list[UUID] = []
    instrument_id: UUID | None = None
    custom_category_id: UUID | None = None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            for index in (1, 2):
                response = await client.post(
                    "/api/v1/portfolios",
                    json={"name": f"Synthetic allocation {suffix}-{index}", "base_currency": "USD"},
                )
                assert response.status_code == 201, response.text
                portfolio_ids.append(UUID(response.json()["id"]))

            instrument = await client.post(
                "/api/v1/instruments",
                json={
                    "name": f"Synthetic allocation equity {suffix}",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "provider_code",
                            "value": suffix,
                            "provider": "phase03_allocation_synthetic",
                        }
                    ],
                },
            )
            assert instrument.status_code == 201, instrument.text
            instrument_id = UUID(instrument.json()["id"])

            for portfolio_id in portfolio_ids:
                default = await client.get(
                    f"/api/v1/portfolios/{portfolio_id}/allocation/"
                    f"instrument-categories/{instrument_id}"
                )
                assert default.status_code == 200, default.text
                assert default.json()["source"] == "default"
                assert default.json()["category"]["system_class"] == "equity"

            created = await client.post(
                f"/api/v1/portfolios/{portfolio_ids[0]}/allocation/categories",
                json={"name": "Synthetic special assets", "system_class": "other"},
            )
            assert created.status_code == 201, created.text
            custom_category_id = UUID(created.json()["id"])

            assigned = await client.put(
                f"/api/v1/portfolios/{portfolio_ids[0]}/allocation/"
                f"instrument-categories/{instrument_id}",
                json={"category_id": str(custom_category_id)},
            )
            assert assigned.status_code == 200, assigned.text
            assert assigned.json()["source"] == "override"
            assert assigned.json()["category"]["id"] == str(custom_category_id)

            other_portfolio = await client.get(
                f"/api/v1/portfolios/{portfolio_ids[1]}/allocation/"
                f"instrument-categories/{instrument_id}"
            )
            assert other_portfolio.status_code == 200, other_portfolio.text
            assert other_portfolio.json()["source"] == "default"
            assert other_portfolio.json()["category"]["system_class"] == "equity"

            protected = await client.delete(
                f"/api/v1/portfolios/{portfolio_ids[0]}/allocation/categories/{custom_category_id}"
            )
            assert protected.status_code == 409
            assert protected.json()["error"]["code"] == "allocation_category_in_use"

            cleared = await client.delete(
                f"/api/v1/portfolios/{portfolio_ids[0]}/allocation/"
                f"instrument-categories/{instrument_id}"
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["source"] == "default"
            deleted = await client.delete(
                f"/api/v1/portfolios/{portfolio_ids[0]}/allocation/categories/{custom_category_id}"
            )
            assert deleted.status_code == 204, deleted.text

        async with sessions() as session:
            override_count = await session.scalar(
                select(func.count())
                .select_from(InstrumentCategoryOverrideModel)
                .where(InstrumentCategoryOverrideModel.instrument_id == instrument_id)
            )
            assert override_count == 0
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(InstrumentCategoryOverrideModel).where(
                    InstrumentCategoryOverrideModel.portfolio_id.in_(portfolio_ids)
                )
            )
            if custom_category_id is not None:
                await session.execute(
                    delete(AllocationCategoryModel).where(
                        AllocationCategoryModel.id == custom_category_id
                    )
                )
            if portfolio_ids:
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id.in_(portfolio_ids))
                )
            if instrument_id is not None:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id == instrument_id)
                )
        application.dependency_overrides.clear()
        await engine.dispose()
