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
    assert openapi_response.json()["info"]["title"] == "Finance Aggregator API"
    live_responses = openapi_response.json()["paths"]["/health/live"]["get"]["responses"]
    assert live_responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
