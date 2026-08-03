import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from shared.errors import install_exception_handlers
from shared.exact import Money


class MoneyPayload(BaseModel):
    amount: Money


@pytest.mark.asyncio
async def test_validation_errors_use_safe_stable_envelope() -> None:
    application = FastAPI()
    install_exception_handlers(application)

    @application.post("/values")
    async def validate_value(payload: MoneyPayload) -> MoneyPayload:
        return payload

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/values", json={"amount": 0.1})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "request_validation_error",
            "message": "Request validation failed",
            "details": [
                {
                    "location": ["body", "amount"],
                    "code": "decimal_string_required",
                    "message": "Exact decimal values must be sent as JSON strings",
                }
            ],
        }
    }
    assert "0.1" not in response.text


@pytest.mark.asyncio
async def test_framework_http_errors_use_same_envelope() -> None:
    application = FastAPI()
    install_exception_handlers(application)
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/missing")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "http_error",
            "message": "Not Found",
            "details": [],
        }
    }
