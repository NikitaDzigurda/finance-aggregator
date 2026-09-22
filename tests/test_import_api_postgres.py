import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from imports.adapters import get_adapter_registry
from imports.jobs import claim_next_job, finish_job
from imports.models import ImportBatchModel
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from instruments.models import InstrumentModel
from portfolios.models import PortfolioModel
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_import_upload_deduplication_preview_and_job_queue(tmp_path: Path) -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(tmp_path / "imports", max_file_size_bytes=1024)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    application.dependency_overrides[get_object_storage] = lambda: storage
    transport = ASGITransport(app=application)
    suffix = uuid4().hex[:8].upper()
    portfolio_id: UUID | None = None
    instrument_id: UUID | None = None
    batch_id: UUID | None = None
    storage_key: str | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio_response = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Import {suffix}", "base_currency": "USD"},
            )
            assert portfolio_response.status_code == 201
            portfolio_id = UUID(portfolio_response.json()["id"])
            account_response = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic broker", "account_type": "broker"},
            )
            assert account_response.status_code == 201
            account_id = account_response.json()["id"]
            isin = f"US{suffix}X1"
            ticker = f"S{suffix}"
            instrument_response = await client.post(
                "/api/v1/instruments",
                json={
                    "name": "Synthetic equity",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [
                        {"identifier_type": "isin", "value": isin},
                        {
                            "identifier_type": "ticker",
                            "value": ticker,
                            "exchange": "XNAS",
                        },
                    ],
                },
            )
            assert instrument_response.status_code == 201, instrument_response.text
            instrument_id = UUID(instrument_response.json()["id"])

            formats_response = await client.get("/api/v1/import-formats")
            assert formats_response.status_code == 200
            assert formats_response.json() == {
                "items": [
                    {
                        "format_id": "alfa_broker_xml_import",
                        "version": "1.0",
                        "supported_file_formats": ["xml"],
                    },
                        {
                            "format_id": "bybit_spot_csv_bundle",
                            "version": "1.0",
                            "supported_file_formats": ["csv"],
                        },
                        {
                            "format_id": "tbank_broker_xlsx",
                        "version": "1.0",
                        "supported_file_formats": ["xlsx"],
                    },
                    {
                        "format_id": "universal_broker",
                        "version": "1.0",
                        "supported_file_formats": ["csv"],
                    }
                ]
            }
            form = {
                "portfolio_id": str(portfolio_id),
                "account_id": account_id,
                "source_provider": "universal_broker",
                "declared_format": "csv",
            }
            content = (
                "Date;Type;Symbol;ISIN;Quantity;Price;Amount;Currency;Fee;"
                "FeeCurrency;Tax;AccruedInterest;Exchange;ExternalId;Note\n"
                f"03.08.2026;Buy;{ticker};{isin};10,5;125,50;;USD;1,25;USD;"
                "2,50;0;XNAS;SYN-1;Synthetic matched row\n"
                "2026-08-04;Dividend;UNKNOWN;GB0000000001;;;12,75;EUR;0;EUR;"
                "0;0;XPAR;SYN-2;Synthetic unknown instrument\n"
            ).encode()

            upload_response = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("../../private.csv", content, "text/csv")},
            )
            assert upload_response.status_code == 201, upload_response.text
            upload = upload_response.json()
            batch_id = UUID(upload["batch"]["id"])
            assert upload["duplicate"] is False
            assert upload["batch"]["original_filename"] == "private.csv"
            assert "storage_key" not in upload["batch"]
            assert upload["batch"]["status"] == "uploaded"

            list_response = await client.get(
                "/api/v1/imports",
                params={
                    "portfolio_id": str(portfolio_id),
                    "account_id": account_id,
                    "status": "uploaded",
                    "limit": 1,
                },
            )
            assert list_response.status_code == 200, list_response.text
            assert list_response.json()["limit"] == 1
            assert list_response.json()["offset"] == 0
            assert [item["id"] for item in list_response.json()["items"]] == [
                str(batch_id)
            ]

            empty_list_response = await client.get(
                "/api/v1/imports",
                params={"portfolio_id": str(portfolio_id), "status": "completed"},
            )
            assert empty_list_response.status_code == 200
            assert empty_list_response.json()["items"] == []

            status_response = await client.get(f"/api/v1/imports/{batch_id}")
            assert status_response.status_code == 200
            assert status_response.json()["jobs"][0]["status"] == "pending"

            preview_response = await client.get(f"/api/v1/imports/{batch_id}/preview")
            assert preview_response.status_code == 200
            assert preview_response.json()["items"] == []
            assert preview_response.json()["total_rows"] == 0

            duplicate_response = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("renamed.csv", content, "text/csv")},
            )
            assert duplicate_response.status_code == 200
            assert duplicate_response.json()["duplicate"] is True
            assert duplicate_response.json()["batch"]["id"] == str(batch_id)

            invalid_response = await client.post(
                "/api/v1/imports",
                data={**form, "declared_format": "pdf"},
                files={"file": ("invalid.pdf", b"not a pdf", "application/pdf")},
            )
            assert invalid_response.status_code == 422
            assert invalid_response.json()["error"]["code"] == "import_file_signature_invalid"

        async with sessions() as session:
            stored_batch = await session.get(ImportBatchModel, batch_id)
            assert stored_batch is not None
            storage_key = stored_batch.storage_key

        async with sessions() as session:
            job = await claim_next_job(session, worker_id="worker-one")
        assert job is not None
        assert job.batch_id == batch_id
        async with sessions() as session:
            assert await claim_next_job(session, worker_id="worker-two") is None

        await process_import_batch(
            sessions,
            storage,
            get_adapter_registry(),
            batch_id=batch_id,
        )
        async with sessions() as session:
            await finish_job(
                session,
                job_id=job.id,
                worker_id="worker-one",
                succeeded=True,
            )

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            processed_status = await client.get(f"/api/v1/imports/{batch_id}")
            assert processed_status.status_code == 200
            assert processed_status.json()["batch"]["status"] == "awaiting_review"
            assert processed_status.json()["batch"]["total_rows"] == 4
            assert processed_status.json()["batch"]["ready_rows"] == 3
            assert processed_status.json()["batch"]["warning_rows"] == 1
            assert processed_status.json()["jobs"][0]["status"] == "succeeded"
            processed_preview = await client.get(f"/api/v1/imports/{batch_id}/preview")
            assert processed_preview.status_code == 200
            assert processed_preview.json()["warning_rows"] == 1
            assert processed_preview.json()["completeness"] == "unknown"
            assert processed_preview.json()["reconciliation_status"] == "not_available"
            assert processed_preview.json()["reconciliation_summary"] is None
            assert processed_preview.json()["items"][0]["raw_data"]["Date"] == "03.08.2026"
            assert processed_preview.json()["items"][0]["normalized_candidate"] == {
                "operation_type": "trade",
                "occurred_at": "2026-08-03T00:00:00Z",
                "time_precision": "date",
                "source_operation_id": "SYN-1",
                "note": "Synthetic matched row",
                "payload": {
                    "side": "buy",
                    "instrument_id": str(instrument_id),
                    "quantity": "10.5",
                    "price": "125.50",
                    "price_currency": "USD",
                },
            }
            assert processed_preview.json()["items"][3]["warnings"] == [
                {
                    "code": "instrument_match_required",
                    "message": "No canonical instrument matched the supplied identifiers",
                }
            ]
            assert processed_preview.json()["summary"] == {
                "operation_counts": {"fee": 1, "income": 1, "tax": 1, "trade": 1},
                "diagnostic_counts": {"instrument_match_required": 1},
                "currency_totals": {
                    "EUR": {
                        "trade_buys": 0,
                        "trade_sells": 0,
                        "income": "12.75",
                        "fees": "0",
                        "taxes": "0",
                        "cash_in": "0",
                        "cash_out": "0",
                    },
                    "USD": {
                        "trade_buys": 1,
                        "trade_sells": 0,
                        "income": "0",
                        "fees": "1.25",
                        "taxes": "2.50",
                        "cash_in": "0",
                        "cash_out": "0",
                    },
                },
            }

            unknown_row_id = processed_preview.json()["items"][3]["id"]
            resolution_response = await client.patch(
                f"/api/v1/imports/{batch_id}/rows/{unknown_row_id}",
                json={
                    "action": "match_instrument",
                    "instrument_id": str(instrument_id),
                    "note": "Synthetic explicit match",
                },
            )
            assert resolution_response.status_code == 200, resolution_response.text
            assert resolution_response.json()["status"] == "ready"
            assert resolution_response.json()["resolutions"][0]["resolution_type"] == (
                "instrument_match"
            )
            resolved_status = await client.get(f"/api/v1/imports/{batch_id}")
            assert resolved_status.json()["batch"]["status"] == "ready_to_commit"
            assert resolved_status.json()["batch"]["ready_rows"] == 4
    finally:
        async with sessions.begin() as session:
            if batch_id is not None:
                await session.execute(
                    delete(ImportBatchModel).where(ImportBatchModel.id == batch_id)
                )
            if portfolio_id is not None:
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
            if instrument_id is not None:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id == instrument_id)
                )
        if storage_key is not None:
            storage.delete(storage_key)
        application.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_xml_upload_security_job_and_deduplication(tmp_path: Path) -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(tmp_path / "xml-imports", max_file_size_bytes=32 * 1024)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    application.dependency_overrides[get_object_storage] = lambda: storage
    transport = ASGITransport(app=application)
    portfolio_id: UUID | None = None
    batch_id: UUID | None = None
    storage_key: str | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio_response = await client.post(
                "/api/v1/portfolios",
                json={"name": f"XML import {uuid4().hex[:8]}", "base_currency": "RUB"},
            )
            assert portfolio_response.status_code == 201
            portfolio_id = UUID(portfolio_response.json()["id"])
            account_response = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic Alfa broker", "account_type": "broker"},
            )
            assert account_response.status_code == 201
            account_id = account_response.json()["id"]
            form = {
                "portfolio_id": str(portfolio_id),
                "account_id": account_id,
                "source_provider": "alfa_broker_xml_import",
                "declared_format": "xml",
            }
            fixture = (
                Path(__file__).parents[1]
                / "docs"
                / "fixtures"
                / "alfa_broker_report_import_synthetic_v1.xml"
            ).read_bytes()

            upload_response = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("synthetic-alfa.xml", fixture, "application/xml; charset=utf-8")},
            )
            assert upload_response.status_code == 201, upload_response.text
            upload = upload_response.json()
            batch_id = UUID(upload["batch"]["id"])
            assert upload["batch"]["declared_format"] == "xml"
            assert upload["batch"]["status"] == "uploaded"
            assert upload["duplicate"] is False

            status_response = await client.get(f"/api/v1/imports/{batch_id}")
            assert status_response.status_code == 200
            assert status_response.json()["jobs"][0]["status"] == "pending"

            duplicate_response = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("renamed.xml", fixture, "text/xml")},
            )
            assert duplicate_response.status_code == 200
            assert duplicate_response.json()["duplicate"] is True
            assert duplicate_response.json()["batch"]["id"] == str(batch_id)

            invalid_content_type = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("synthetic-alfa.xml", fixture, "text/html")},
            )
            assert invalid_content_type.status_code == 415
            assert invalid_content_type.json()["error"]["code"] == (
                "import_content_type_invalid"
            )

            invalid_extension = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("synthetic-alfa.html", fixture, "application/xml")},
            )
            assert invalid_extension.status_code == 422
            assert invalid_extension.json()["error"]["code"] == "import_extension_mismatch"

            unsafe_payloads = [
                (
                    b"<html><body>PRIVATE-MARKER</body></html>",
                    "import_file_signature_invalid",
                ),
                (
                    b'<?xml version="1.0"?><!DOCTYPE report_broker '
                    b'[<!ENTITY x "PRIVATE-MARKER">]><report_broker>&x;</report_broker>',
                    "import_xml_dtd_forbidden",
                ),
                (
                    b"<report_broker>"
                    + b"<level>" * 64
                    + b"PRIVATE-MARKER"
                    + b"</level>" * 64
                    + b"</report_broker>",
                    "import_xml_depth_limit_exceeded",
                ),
            ]
            for index, (payload, expected_code) in enumerate(unsafe_payloads):
                rejected = await client.post(
                    "/api/v1/imports",
                    data=form,
                    files={"file": (f"unsafe-{index}.xml", payload, "application/xml")},
                )
                assert rejected.status_code == 422
                assert rejected.json()["error"]["code"] == expected_code
                assert "PRIVATE-MARKER" not in rejected.text

        async with sessions() as session:
            stored_batch = await session.get(ImportBatchModel, batch_id)
            assert stored_batch is not None
            storage_key = stored_batch.storage_key
            assert stored_batch.sha256 == upload["batch"]["sha256"]
            assert storage.exists(storage_key)
    finally:
        async with sessions.begin() as session:
            if batch_id is not None:
                await session.execute(
                    delete(ImportBatchModel).where(ImportBatchModel.id == batch_id)
                )
            if portfolio_id is not None:
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
        if storage_key is not None:
            storage.delete(storage_key)
        application.dependency_overrides.clear()
        await engine.dispose()
