import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.db_types import MoneyNumeric, PriceNumeric, QuantityNumeric, RateNumeric
from shared.errors import install_exception_handlers
from shared.exact import (
    AssetIdentifier,
    AwareDateTime,
    CurrencyCode,
    Money,
    Price,
    Quantity,
    Rate,
    TimePrecision,
)


class ExactRoundTrip(BaseModel):
    money: Money
    quantity: Quantity
    price: Price
    rate: Rate
    currency: CurrencyCode
    asset_id: AssetIdentifier
    occurred_at: AwareDateTime
    time_precision: TimePrecision


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_exact_values_round_trip_api_postgres_api_without_change() -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    metadata = MetaData()
    probe = Table(
        "test_exact_value_roundtrip",
        metadata,
        Column("money", MoneyNumeric(), nullable=False),
        Column("quantity", QuantityNumeric(), nullable=False),
        Column("price", PriceNumeric(), nullable=False),
        Column("rate", RateNumeric(), nullable=False),
        Column("currency", String(3), nullable=False),
        Column("asset_id", Uuid(as_uuid=True), nullable=False),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("time_precision", String(16), nullable=False),
    )

    application = FastAPI()
    install_exception_handlers(application)

    @application.post("/exact-values", response_model=ExactRoundTrip)
    async def store_exact_values(payload: ExactRoundTrip) -> ExactRoundTrip:
        async with sessions.begin() as session:
            result = await session.execute(
                insert(probe).values(**payload.model_dump()).returning(*probe.c)
            )
            stored = result.mappings().one()
        return ExactRoundTrip.model_validate(dict(stored))

    payload = {
        "money": "12345678901234567890.123456789012345678",
        "quantity": "0.000000000000000001",
        "price": "99999999999999999999.999999999999999999",
        "rate": "12345678901234.123456789012345678901234",
        "currency": "USD",
        "asset_id": "11111111-2222-4333-8444-555555555555",
        "occurred_at": "2026-08-03T10:30:15.123456Z",
        "time_precision": "microsecond",
    }

    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
            await connection.run_sync(metadata.create_all)

        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/exact-values", json=payload)

        assert response.status_code == 200, response.text
        assert response.json() == payload
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
        await engine.dispose()
