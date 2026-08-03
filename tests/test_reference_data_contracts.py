import pytest
from pydantic import ValidationError

from accounts.models import AccountType
from accounts.schemas import AccountCreate
from instruments.schemas import InstrumentCreate
from portfolios.schemas import PortfolioCreate


def test_phase_three_contracts_accept_supported_reference_data() -> None:
    portfolio = PortfolioCreate.model_validate(
        {"name": "Long-term capital", "base_currency": "USD"}
    )
    accounts = [
        AccountCreate.model_validate(
            {"name": account_type.value, "account_type": account_type.value}
        )
        for account_type in AccountType
    ]
    instrument = InstrumentCreate.model_validate(
        {
            "name": "Synthetic Equity",
            "instrument_type": "stock",
            "currency": "USD",
            "identifiers": [
                {"identifier_type": "isin", "value": "US0000000001"},
                {
                    "identifier_type": "ticker",
                    "value": "SYN",
                    "exchange": "XNAS",
                },
            ],
        }
    )

    assert portfolio.base_currency == "USD"
    assert [account.account_type for account in accounts] == list(AccountType)
    assert len(instrument.identifiers) == 2


@pytest.mark.parametrize(
    ("identifier", "expected_code"),
    [
        ({"identifier_type": "ticker", "value": "SYN"}, "identifier_exchange_required"),
        (
            {"identifier_type": "isin", "value": "invalid"},
            "identifier_format",
        ),
        (
            {
                "identifier_type": "provider_code",
                "value": "abc-123",
                "provider": "Broker Name",
            },
            "identifier_provider_format",
        ),
    ],
)
def test_instrument_identifier_scope_and_format_are_explicit(
    identifier: dict[str, str],
    expected_code: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        InstrumentCreate.model_validate(
            {
                "name": "Synthetic Instrument",
                "instrument_type": "stock",
                "currency": "USD",
                "identifiers": [identifier],
            }
        )

    assert error.value.errors()[0]["type"] == expected_code


def test_duplicate_identifiers_in_one_request_are_rejected() -> None:
    identifier = {"identifier_type": "isin", "value": "US0000000001"}

    with pytest.raises(ValidationError) as error:
        InstrumentCreate.model_validate(
            {
                "name": "Synthetic Instrument",
                "instrument_type": "stock",
                "currency": "USD",
                "identifiers": [identifier, identifier],
            }
        )

    assert error.value.errors()[0]["type"] == "duplicate_identifier"
