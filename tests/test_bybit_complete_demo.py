import importlib.util
import os
from decimal import Decimal as D
from decimal import localcontext
from fractions import Fraction as F
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.main import create_app
from imports.adapters import get_adapter_registry
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from shared.database import get_db_session

ROOT = Path(__file__).parents[1] / "docs/fixtures/bybit_complete"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_complete_bybit_acquisition_pnl_and_no_duplicate_import(tmp_path):
    url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("dedicated test database required")
    spec = importlib.util.spec_from_file_location("complete_demo", ROOT / "load_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        sessions = async_sessionmaker(
            connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        app = create_app()
        storage = LocalObjectStorage(tmp_path / "reports", max_file_size_bytes=1024 * 1024)

        async def db():
            async with sessions() as session:
                yield session

        async def process(bid):
            await process_import_batch(
                sessions, storage, get_adapter_registry(), batch_id=UUID(bid)
            )

        app.dependency_overrides[get_db_session] = db
        app.dependency_overrides[get_object_storage] = lambda: storage
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                result = await module.load(client, "Complete Bybit synthetic test", process)
                snapshot, overview = result["snapshot"], result["overview"]
                assert snapshot["operation_count"] == 28
                fetched = await client.get(f"/api/v1/portfolios/{result['portfolio_id']}/positions")
                assert fetched.json() == snapshot
                assert not {d["code"] for d in snapshot["diagnostics"]} & {
                    "negative_position",
                    "cost_basis_unknown",
                    "market_price_missing",
                }
                # Independent rational arithmetic, without the calculation engine.
                qty, basis = {"USDT": F(5000)}, {"USDT": F(5000)}
                for n in range(12):
                    coin = "QAX" if n % 2 == 0 else "QAY"
                    buy = n < 8
                    q, price = F(5 if buy else 2), F(20 + n)
                    sold, bought = ("USDT", coin) if buy else (coin, "USDT")
                    out, incoming = (q * price, q) if buy else (q, q * price)
                    carried = basis[sold] * out / qty[sold]
                    basis[sold] -= carried
                    qty[sold] -= out
                    qty[bought] = qty.get(bought, F(0)) + incoming
                    basis[bought] = basis.get(bought, F(0)) + carried
                    fee = incoming / 1000
                    basis[bought] *= (qty[bought] - fee) / qty[bought]
                    qty[bought] -= fee
                basis["USDT"] *= (qty["USDT"] - F("100.1")) / qty["USDT"]
                qty["USDT"] -= F("100.1")
                with localcontext() as ctx:
                    ctx.prec = 80
                    for code, instrument_id in result["assets"].items():
                        position = next(
                            p for p in snapshot["positions"] if p["instrument_id"] == instrument_id
                        )
                        assert D(position["quantity"]) == D(qty[code].numerator) / D(
                            qty[code].denominator
                        )
                        exact_basis = D(basis[code].numerator) / D(basis[code].denominator)
                        assert abs(D(position["cost_basis"]) - exact_basis) < D(
                            "0.0000000000000001"
                        )
                for metric in ("cost_basis", "realised_pnl", "unrealised_pnl", "current_value"):
                    assert overview[metric]["quality"] == "complete"
                assert D(overview["current_value"]["total_value"]) == D("5170.444")
                assert D(overview["realised_pnl"]["total_value"]) == 0
                assert D(overview["unrealised_pnl"]["total_value"]) > 0
                repeated = await client.post(f"/api/v1/imports/{result['batch_id']}/confirm")
                assert repeated.json()["idempotent"] is True
                duplicate = await client.post(
                    "/api/v1/imports",
                    data={
                        "portfolio_id": result["portfolio_id"],
                        "account_id": result["account_id"],
                        "source_provider": "bybit_spot_csv_bundle",
                        "declared_format": "csv",
                    },
                    files=[
                        ("file", (p.name, p.read_bytes(), "text/csv"))
                        for p in sorted((ROOT / "csv").glob("*.csv"))
                    ],
                )
                assert duplicate.json()["duplicate"] is True
        finally:
            await transaction.rollback()
    await engine.dispose()
