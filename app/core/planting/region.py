"""시군구 → 기후권(zone) 매핑.

부록 B 의 region_rules 역할. v1 은 광역시·도 단위 zone 분류만 제공하고
월 보정(adjust)은 보수적으로 0(미보정)으로 둔다. 시군구 단위 주차 보정은
region_rules.json + 기상청 평년값 연동 시 확장한다(과도한 임의 보정 방지).
"""

from __future__ import annotations

# 광역시·도 → 기후권. (engine.PROVINCE_CLIMATE 의 서리위험과 정합)
PROVINCE_ZONE: dict[str, str] = {
    "제주특별자치도": "제주",
    "부산광역시": "남부",
    "울산광역시": "남부",
    "대구광역시": "남부",
    "전라남도": "남부",
    "경상남도": "남부",
    "광주광역시": "남부",
    "전북특별자치도": "중부",
    "대전광역시": "중부",
    "경상북도": "중부",
    "충청남도": "중부",
    "세종특별자치시": "중부",
    "서울특별시": "중부",
    "충청북도": "중부",
    "인천광역시": "중부",
    "경기도": "중부",
    "강원특별자치도": "고랭지",
}
DEFAULT_ZONE = "중부"


def zone_of(sigungu: str) -> str:
    """'경기도 성남시' → '중부'. province 토큰 우선, 없으면 부분일치, 기본 중부."""
    s = (sigungu or "").strip()
    if not s:
        return DEFAULT_ZONE
    head = s.split()[0]
    if head in PROVINCE_ZONE:
        return PROVINCE_ZONE[head]
    for prov, zone in PROVINCE_ZONE.items():
        if prov.startswith(head) or head in prov or prov[:2] in s:
            return zone
    return DEFAULT_ZONE


def adjust(month: int, zone: str) -> int:
    """기후권 보정 월. v1 미보정(0). region_rules.json 연동 시 확장."""
    del zone
    return month
