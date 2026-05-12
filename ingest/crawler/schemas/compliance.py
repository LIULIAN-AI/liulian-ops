from crawler.schemas._base import _Base
from crawler.schemas._schema_factory import build_models

_CLASS_NAMES = ("Initiative", "Regulatory")

_MODELS = build_models("compliance", _CLASS_NAMES)

Initiative: type[_Base] = _MODELS["Initiative"]
Regulatory: type[_Base] = _MODELS["Regulatory"]

__all__ = _CLASS_NAMES
