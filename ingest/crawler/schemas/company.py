from crawler.schemas._base import _Base
from crawler.schemas._schema_factory import build_models

_CLASS_NAMES = (
    "Company",
    "Finance",
    "CompanyTag",
    "CompanyNews",
    "CompanyNewsTag",
    "Director",
    "Funding",
    "Investment",
    "License",
    "LicenseObtainment",
    "Location",
    "CompanyLocation",
    "CompanyOwner",
    "IOSApp",
    "Marketing",
    "Web3",
    "Shareholder",
    "ShareholderTag",
    "ManagementTeamStaff",
    "ManagementTeamTag",
    "Report",
    "Campaign",
    "CampaignUrl",
)

_MODELS = build_models("company", _CLASS_NAMES)

Company: type[_Base] = _MODELS["Company"]
Finance: type[_Base] = _MODELS["Finance"]
CompanyTag: type[_Base] = _MODELS["CompanyTag"]
CompanyNews: type[_Base] = _MODELS["CompanyNews"]
CompanyNewsTag: type[_Base] = _MODELS["CompanyNewsTag"]
Director: type[_Base] = _MODELS["Director"]
Funding: type[_Base] = _MODELS["Funding"]
Investment: type[_Base] = _MODELS["Investment"]
License: type[_Base] = _MODELS["License"]
LicenseObtainment: type[_Base] = _MODELS["LicenseObtainment"]
Location: type[_Base] = _MODELS["Location"]
CompanyLocation: type[_Base] = _MODELS["CompanyLocation"]
CompanyOwner: type[_Base] = _MODELS["CompanyOwner"]
IOSApp: type[_Base] = _MODELS["IOSApp"]
Marketing: type[_Base] = _MODELS["Marketing"]
Web3: type[_Base] = _MODELS["Web3"]
Shareholder: type[_Base] = _MODELS["Shareholder"]
ShareholderTag: type[_Base] = _MODELS["ShareholderTag"]
ManagementTeamStaff: type[_Base] = _MODELS["ManagementTeamStaff"]
ManagementTeamTag: type[_Base] = _MODELS["ManagementTeamTag"]
Report: type[_Base] = _MODELS["Report"]
Campaign: type[_Base] = _MODELS["Campaign"]
CampaignUrl: type[_Base] = _MODELS["CampaignUrl"]

__all__ = _CLASS_NAMES
