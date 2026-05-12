from crawler.schemas._base import _Base
from crawler.schemas._schema_factory import build_models

_CLASS_NAMES = (
    "Financials",
    "FFunding",
    "FInvestment",
    "FinancialData",
    "Bank",
    "InvestorInfo",
)

_MODELS = build_models("financials", _CLASS_NAMES)

Financials: type[_Base] = _MODELS["Financials"]
FFunding: type[_Base] = _MODELS["FFunding"]
FInvestment: type[_Base] = _MODELS["FInvestment"]
FinancialData: type[_Base] = _MODELS["FinancialData"]
Bank: type[_Base] = _MODELS["Bank"]
InvestorInfo: type[_Base] = _MODELS["InvestorInfo"]

__all__ = _CLASS_NAMES
