from crawler.schemas._base import _Base
from crawler.schemas._schema_factory import build_models

_CLASS_NAMES = (
    "StaffEmployeeNoAndTech",
    "StaffManagement",
    "StaffShareholder",
)

_MODELS = build_models("staff", _CLASS_NAMES)

StaffEmployeeNoAndTech: type[_Base] = _MODELS["StaffEmployeeNoAndTech"]
StaffManagement: type[_Base] = _MODELS["StaffManagement"]
StaffShareholder: type[_Base] = _MODELS["StaffShareholder"]

__all__ = _CLASS_NAMES
