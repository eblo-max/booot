"""Преобразование CompanyDTO <-> ORM-модель."""

from src.db.models import Company
from src.domain.company import CompanyDTO
from src.domain.tax_status import TaxStatus

_FIELDS = (
    "ogrn", "inn", "name", "full_name", "opf", "status", "region_code", "region_name",
    "registration_date", "main_okved", "okved_list", "revenue_year", "revenue", "profit",
    "tax_regimes", "tax_source", "phones", "emails", "website", "manager_name",
    "source", "source_updated_at",
)


def dto_to_values(dto: CompanyDTO) -> dict:
    values = {f: getattr(dto, f) for f in _FIELDS}
    values["tax_status"] = str(dto.tax_status)
    values["raw_data_json"] = dto.raw or None
    return values


def model_to_dto(model: Company) -> CompanyDTO:
    return CompanyDTO(
        ogrn=model.ogrn,
        inn=model.inn,
        name=model.name,
        full_name=model.full_name,
        opf=model.opf,
        status=model.status,
        region_code=model.region_code,
        region_name=model.region_name,
        registration_date=model.registration_date,
        main_okved=model.main_okved,
        okved_list=model.okved_list or [],
        revenue_year=model.revenue_year,
        revenue=model.revenue,
        profit=model.profit,
        tax_status=TaxStatus(model.tax_status),
        tax_regimes=model.tax_regimes or [],
        tax_source=model.tax_source,
        phones=model.phones or [],
        emails=model.emails or [],
        website=model.website,
        manager_name=model.manager_name,
        source=model.source,
        source_updated_at=model.source_updated_at,
        raw=model.raw_data_json or {},
    )
