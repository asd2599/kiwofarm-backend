"""정부 지원사업 매칭 스키마."""

from pydantic import BaseModel, Field


class ApplyInfo(BaseModel):
    where: str = ""
    link: str = ""
    phone: str = ""


class ProgramOut(BaseModel):
    id: int
    name: str
    agency: str = ""
    category: str = ""
    summary: str = ""
    support: str = ""
    status: str                      # 'eligible' | 'check'
    reasons: list[str] = Field(default_factory=list)
    notes: str = ""
    audience: list[str] = Field(default_factory=list)
    apply: ApplyInfo = Field(default_factory=ApplyInfo)
    source_url: str = ""


class SupportMatchResponse(BaseModel):
    found: bool
    mode: str
    age: int | None = None
    province: str | None = None
    advice: str = ""
    advice_source: str = "none"      # 'ai' | 'rule' | 'none'
    eligible_count: int = 0
    check_count: int = 0
    excluded_count: int = 0
    programs: list[ProgramOut] = Field(default_factory=list)
    message: str | None = None
