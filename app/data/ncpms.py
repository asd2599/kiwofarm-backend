"""국가농작물병해충관리시스템(NCPMS) Open API 클라이언트.

베이스: http://ncpms.rda.go.kr/npmsAPI/service
인증키 파라미터명: apiKey (settings.ncpms_api_key)

이 모듈은 작목명으로 병해충 도감(SVC01)을 조회해 증상·방제 정보를 회수한다.
RAG 인제스트의 "병해충 예방" 근거를 GPT 일반지식 대신 실제 RDA 데이터로 보강하는 용도.

서비스 구조 (NCPMS npmsAPI):
  serviceCode=SVC01  병해충 검색(도감)
    serviceType=AA001  목록 조회 (cropName 등으로 검색) → sickKey 목록
    serviceType=AA002  상세 조회 (sickKey) → 증상/방제/발생환경

주의:
  - 응답 태그명이 사양 개정에 따라 달라질 수 있어 파싱은 방어적으로(존재하는 필드만 사용).
  - ncpms_api_key 가 비어 있으면 모든 함수가 빈 결과를 돌려준다(상위 RAG가 자동 skip).
  - 키가 미등록이면 NCPMS 는 errorCode=ERR_101 을 반환 → NcpmsError 로 표면화.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE_URL = "http://ncpms.rda.go.kr/npmsAPI/service"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_RETRIES = 2

SERVICE_PEST = "SVC01"  # 병해충 검색(도감)
TYPE_LIST = "AA001"
TYPE_DETAIL = "AA002"

# 상세 응답에서 RAG 본문으로 쓸 후보 필드 (라벨, 태그 후보들).
# 태그명 변동에 대비해 여러 후보를 두고 먼저 잡히는 것을 사용.
_DETAIL_FIELDS: list[tuple[str, tuple[str, ...]]] = [
    ("병해충명", ("sickNameKor", "sickNameKorThum", "pestNameKor", "insectKorName")),
    ("학명/영문", ("sickNameEng", "sickNameChi", "pestNameEng")),
    ("기주작물", ("cropName",)),
    ("병징/증상", ("symptoms", "symptomText", "sickSymptomDc")),
    ("발생생태/환경", ("developmentCondition", "environmentText", "ocrnEcolgyDc")),
    ("방제방법", ("preventionMethod", "controlMethod", "preventMethodText", "cprMthd")),
]


class NcpmsError(RuntimeError):
    """NCPMS 호출 실패 (키 미등록·네트워크·파싱·errorCode 등)."""


def _enabled() -> bool:
    return bool(settings.ncpms_api_key)


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _first(item: ET.Element, tags: tuple[str, ...]) -> str:
    for tag in tags:
        v = _text(item.find(tag))
        if v:
            return v
    return ""


async def _call(params: dict[str, str]) -> ET.Element:
    """NCPMS 호출 → XML 루트. errorCode 가 있으면 NcpmsError."""
    merged = {"apiKey": settings.ncpms_api_key, **params}
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(BASE_URL, params=merged)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except (httpx.HTTPError, ET.ParseError) as e:
            last_err = e
            await asyncio.sleep(0.5 * (2**attempt))
            continue

        err = _text(root.find(".//errorCode"))
        if err:
            msg = _text(root.find(".//errorMsg"))
            raise NcpmsError(f"NCPMS errorCode={err} {msg}")
        return root
    raise NcpmsError(f"NCPMS 네트워크/파싱 실패: {last_err}")


@dataclass(frozen=True)
class PestHit:
    sick_key: str
    name: str
    crop_name: str


async def search_pests(crop_name: str, *, limit: int = 8) -> list[PestHit]:
    """작목명으로 병해충 목록 조회. 키 없으면 빈 리스트."""
    if not _enabled() or not crop_name.strip():
        return []
    root = await _call(
        {
            "serviceCode": SERVICE_PEST,
            "serviceType": TYPE_LIST,
            "cropName": crop_name.strip(),
            "displayCount": str(max(1, min(limit, 50))),
            "startPoint": "1",
        }
    )
    hits: list[PestHit] = []
    for item in root.findall(".//list/item") or root.findall(".//item"):
        sick_key = _first(item, ("sickKey", "pestKey", "key"))
        if not sick_key:
            continue
        hits.append(
            PestHit(
                sick_key=sick_key,
                name=_first(item, ("sickNameKor", "pestNameKor", "insectKorName")),
                crop_name=_first(item, ("cropName",)) or crop_name,
            )
        )
        if len(hits) >= limit:
            break
    return hits


async def fetch_pest_detail_text(sick_key: str) -> str:
    """병해충 상세를 RAG 본문용 텍스트 블록으로. 실패/빈 응답이면 빈 문자열."""
    if not _enabled() or not sick_key:
        return ""
    root = await _call(
        {"serviceCode": SERVICE_PEST, "serviceType": TYPE_DETAIL, "sickKey": sick_key}
    )
    lines: list[str] = []
    for label, tags in _DETAIL_FIELDS:
        val = _first(root, tags)
        if val:
            # 본문에 태그 잔여물/과다 공백 정리
            val = " ".join(val.split())
            lines.append(f"{label}: {val}")
    return "\n".join(lines)


async def fetch_pest_texts(crop_name: str, *, limit: int = 8) -> list[str]:
    """작목의 병해충 도감을 RAG 청크용 텍스트 리스트로 회수.

    상위(ingest)에서 그대로 임베딩한다. 키 없거나 결과 없으면 빈 리스트.
    개별 병해충 상세 실패는 건너뛰고 가능한 것만 모은다.
    """
    if not _enabled():
        return []
    try:
        hits = await search_pests(crop_name, limit=limit)
    except NcpmsError as e:
        log.info("NCPMS 병해충 검색 실패 crop=%s reason=%s", crop_name, e)
        return []

    async def _detail(hit: PestHit) -> str:
        try:
            body = await fetch_pest_detail_text(hit.sick_key)
        except NcpmsError:
            body = ""
        if not body:
            return ""
        header = f"[{crop_name} 병해충] {hit.name}".strip()
        return f"{header}\n{body}"

    results = await asyncio.gather(*(_detail(h) for h in hits))
    return [r for r in results if r]


__all__ = [
    "NcpmsError",
    "PestHit",
    "search_pests",
    "fetch_pest_detail_text",
    "fetch_pest_texts",
]
