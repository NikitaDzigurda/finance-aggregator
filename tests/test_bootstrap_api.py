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
        "calculation",
        "imports",
    }
    trade_example = schema["paths"]["/api/v1/operations"]["post"]["requestBody"][
        "content"
    ]["application/json"]["examples"]["trade"]["value"]
    assert trade_example["payload"]["quantity"] == "10"
    assert trade_example["payload"]["price"] == "125.50"
    live_responses = schema["paths"]["/health/live"]["get"]["responses"]
    assert live_responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
