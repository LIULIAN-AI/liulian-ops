from crawler.schemas._base import _Base
from crawler.schemas._schema_factory import build_models

_CLASS_NAMES = (
    "MarketingAppLatestRecord",
    "MarketingBaseHeader",
    "MarketingMediaInfo",
    "MarketingSocialMediaDetail",
)

_MODELS = build_models("marketing", _CLASS_NAMES)

MarketingAppLatestRecord: type[_Base] = _MODELS["MarketingAppLatestRecord"]
MarketingBaseHeader: type[_Base] = _MODELS["MarketingBaseHeader"]
MarketingMediaInfo: type[_Base] = _MODELS["MarketingMediaInfo"]
MarketingSocialMediaDetail: type[_Base] = _MODELS["MarketingSocialMediaDetail"]

__all__ = _CLASS_NAMES
