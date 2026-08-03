from accounts.models import AccountModel as _AccountModel
from imports.models import ImportBatchModel as _ImportBatchModel
from imports.models import ImportJobModel as _ImportJobModel
from imports.models import ImportResolutionModel as _ImportResolutionModel
from imports.models import ImportRowModel as _ImportRowModel
from instruments.models import InstrumentIdentifierModel as _InstrumentIdentifierModel
from instruments.models import InstrumentModel as _InstrumentModel
from operations.models import OperationModel as _OperationModel
from portfolios.models import PortfolioModel as _PortfolioModel
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
)


def load_domain_models() -> None:
    if any(model.metadata is not Base.metadata for model in DOMAIN_MODELS):
        raise RuntimeError("Domain models must share Base.metadata")
