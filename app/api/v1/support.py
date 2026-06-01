"""정부 지원사업 매칭 API.

온보딩 조건(귀농/주말, 연령, 영농경력)으로 귀농·농업 정책자금을 추천한다.
"""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.core.support.advice import generate_support_advice
from app.core.support.match import match_programs
from app.schemas.support import ApplyInfo, ProgramOut, SupportMatchResponse

router = APIRouter(prefix="/support", tags=["support"])


@router.get("/match", response_model=None)
async def support_match_endpoint(
    mode: str = Query("returning", description="returning(귀농) | weekend(주말농장)"),
    age: int | None = Query(None, ge=0, le=120),
    farming_years: int | None = Query(None, ge=0, le=80),
    province: str | None = Query(None, description="시·도 (표시·AI용)"),
) -> JSONResponse:
    """조건 매칭된 지원사업 + AI 맞춤 요약."""
    matched, excluded = match_programs(mode=mode, age=age, farming_years=farming_years)
    advice, source = await generate_support_advice(mode, age, province, matched)

    programs = [
        ProgramOut(
            id=m.program["id"],
            name=m.program["name"],
            agency=m.program.get("agency", ""),
            category=m.program.get("category", ""),
            summary=m.program.get("summary", ""),
            support=m.program.get("support", ""),
            status=m.status,
            reasons=m.reasons,
            notes=m.program.get("eligibility", {}).get("notes", ""),
            audience=m.program.get("eligibility", {}).get("audience", []),
            apply=ApplyInfo(**m.program.get("apply", {})),
            source_url=m.program.get("source_url", ""),
        )
        for m in matched
    ]

    payload = SupportMatchResponse(
        found=bool(matched),
        mode=mode,
        age=age,
        province=province,
        advice=advice,
        advice_source=source,
        eligible_count=sum(1 for m in matched if m.status == "eligible"),
        check_count=sum(1 for m in matched if m.status == "check"),
        excluded_count=excluded,
        programs=programs,
        message=None if matched else "조건에 맞는 지원사업을 찾지 못했습니다.",
    )
    return JSONResponse(payload.model_dump(mode="json"))
