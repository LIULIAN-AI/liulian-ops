from crawler.schemas._base import _Base
from crawler.schemas._schema_factory import build_models

_CLASS_NAMES = (
    "BankAccount",
    "Card",
    "CardWelfare",
    "CompanyProduct",
    "Deposit",
    "InterestRate",
    "Lending",
    "Partner",
    "TransferAndExchange",
)

_MODELS = build_models("product", _CLASS_NAMES)

BankAccount: type[_Base] = _MODELS["BankAccount"]
Card: type[_Base] = _MODELS["Card"]
CardWelfare: type[_Base] = _MODELS["CardWelfare"]
CompanyProduct: type[_Base] = _MODELS["CompanyProduct"]
Deposit: type[_Base] = _MODELS["Deposit"]
InterestRate: type[_Base] = _MODELS["InterestRate"]
Lending: type[_Base] = _MODELS["Lending"]
Partner: type[_Base] = _MODELS["Partner"]
TransferAndExchange: type[_Base] = _MODELS["TransferAndExchange"]

__all__ = _CLASS_NAMES
