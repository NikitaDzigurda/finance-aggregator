from accounts.models import AccountModel as _AccountModel
from calculation.models import CalculatedCashBalanceModel as _CalculatedCashBalanceModel
from calculation.models import CalculatedCurrencyMetricsModel as _CalculatedCurrencyMetricsModel
from calculation.models import CalculatedPositionModel as _CalculatedPositionModel
from calculation.models import CalculationSnapshotModel as _CalculationSnapshotModel
from imports.models import ImportBatchModel as _ImportBatchModel
from imports.models import ImportJobModel as _ImportJobModel
from imports.models import ImportResolutionModel as _ImportResolutionModel
from imports.models import ImportRowModel as _ImportRowModel
from instruments.models import InstrumentIdentifierModel as _InstrumentIdentifierModel
from instruments.models import InstrumentModel as _InstrumentModel
from operations.models import OperationModel as _OperationModel
from portfolios.models import PortfolioModel as _PortfolioModel
from pricing.models import MarketPriceModel as _MarketPriceModel
from shared.database import Base

DOMAIN_MODELS = (
    _PortfolioModel,
    _AccountModel,
    _InstrumentModel,
    _InstrumentIdentifierModel,
    _OperationModel,
    _ImportBatchModel,
    _ImportRowModel,
    _ImportResolutionModel,
    _ImportJobModel,
    _MarketPriceModel,
    _CalculationSnapshotModel,
    _CalculatedPositionModel,
    _CalculatedCashBalanceModel,
    _CalculatedCurrencyMetricsModel,
)


def load_domain_models() -> None:
    if any(model.metadata is not Base.metadata for model in DOMAIN_MODELS):
        raise RuntimeError("Domain models must share Base.metadata")
