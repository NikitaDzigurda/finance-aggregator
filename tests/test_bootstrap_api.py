import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app


@pytest.mark.asyncio
async def test_bootstrap_http_surface_is_available() -> None:
    """Smoke-test only the public endpoints required by bootstrap Definition of Done."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        live_response = await client.get("/health/live")
        docs_response = await client.get("/docs")
        openapi_response = await client.get("/openapi.json")

    assert live_response.status_code == 200
    assert live_response.json() == {"status": "ok"}
    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    schema = openapi_response.json()
    assert schema["info"]["title"] == "Finance Aggregator API"
    operations = [
        operation
        for methods in schema["paths"].values()
        for method, operation in methods.items()
        if method in {"get", "post", "patch", "delete", "put"}
    ]
    assert all(operation.get("summary") for operation in operations)
    assert all(operation.get("description") for operation in operations)
    assert {tag["name"] for tag in schema["tags"]} == {
        "health",
        "portfolios",
        "accounts",
        "instruments",
            "operations",
            "pricing",
            "allocation",
            "analytics",
            "calculation",
        "imports",
    }
    trade_example = schema["paths"]["/api/v1/operations"]["post"]["requestBody"][
        "content"
    ]["application/json"]["examples"]["trade"]["value"]
    assert trade_example["payload"]["quantity"] == "10"
    assert trade_example["payload"]["price"] == "125.50"
    upload_schema = schema["components"]["schemas"][
        "Body_upload_import_route_api_v1_imports_post"
    ]["properties"]
    assert upload_schema["source_provider"]["examples"] == [
        "tbank_broker_xlsx",
        "alfa_broker_xml_import",
        "bybit_spot_csv_bundle",
        "universal_broker",
    ]
    assert upload_schema["declared_format"]["examples"] == ["xlsx", "xml"]
    preview_properties = schema["components"]["schemas"]["ImportPreviewResponse"][
        "properties"
    ]
    assert {
        "reporting_period_start",
        "reporting_period_end",
        "completeness",
        "reconciliation_status",
        "reconciliation_summary",
    } <= preview_properties.keys()
    preview_summary = schema["components"]["schemas"]["ImportPreviewSummary"]
    assert "diagnostic_counts" in preview_summary["properties"]
    live_responses = schema["paths"]["/health/live"]["get"]["responses"]
    assert live_responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }

    analytics_paths = (
        "/api/v1/portfolios/{portfolio_id}/analytics/overview",
        "/api/v1/portfolios/{portfolio_id}/analytics/holdings",
        "/api/v1/portfolios/{portfolio_id}/analytics/breakdown",
        "/api/v1/portfolios/{portfolio_id}/analytics/exposure",
        "/api/v1/portfolios/{portfolio_id}/allocation",
        "/api/v1/portfolios/{portfolio_id}/analytics/data-quality",
        "/api/v1/portfolios/{portfolio_id}/analytics/cash-flows",
        "/api/v1/portfolios/{portfolio_id}/analytics/income",
        "/api/v1/portfolios/{portfolio_id}/analytics/costs",
        "/api/v1/portfolios/{portfolio_id}/analytics/trading",
    )
    for path in analytics_paths:
        response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"].startswith("#/components/schemas/")

    overview_content = schema["paths"][analytics_paths[0]]["get"]["responses"]["200"][
        "content"
    ]["application/json"]
    assert set(overview_content["examples"]) == {"complete", "partial", "empty"}
    assert overview_content["examples"]["complete"]["value"]["current_value"][
        "quality"
    ] == "complete"
    assert overview_content["examples"]["partial"]["value"]["current_value"][
        "total_value"
    ] is None
    assert overview_content["examples"]["empty"]["value"]["current_value"][
        "known_value"
    ] == "0.000000000000000000"

    timeline_operation = schema["paths"][analytics_paths[6]]["get"]
    timeline_parameters = {item["name"]: item for item in timeline_operation["parameters"]}
    assert set(timeline_parameters) == {
        "portfolio_id",
        "from",
        "to",
        "bucket",
        "timezone",
        "unit_type",
        "unit",
        "limit",
        "offset",
    }
    assert timeline_parameters["limit"]["schema"]["maximum"] == 100
    assert timeline_parameters["limit"]["schema"]["default"] == 100
    assert timeline_parameters["offset"]["schema"]["minimum"] == 0
    timeline_response = schema["components"]["schemas"]["EventTimelineResponse"]
    assert {"series_total", "limit", "offset", "series"} <= set(
        timeline_response["properties"]
    )
    assert "key" in schema["components"]["schemas"]["TimelineSeriesResponse"][
        "properties"
    ]
