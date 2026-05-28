from datetime import date, timedelta

from fastapi import APIRouter

from app.schemas.shipping import (
    PeerFarmStat,
    PricePoint,
    ShippingDashboard,
    ShippingDecision,
)

router = APIRouter(prefix="/shipping", tags=["shipping"])

CROP_NAMES = {
    "tomato": "토마토",
    "sweetpotato": "고구마",
    "blueberry": "블루베리",
}


def _mock_series(today: date, base: int = 4200) -> list[PricePoint]:
    """14일 과거 실측 + 14일 미래 예측. KAMIS/Prophet 연동 전 mock."""
    series: list[PricePoint] = []

    for i in range(-14, 0):
        d = today + timedelta(days=i)
        price = base + (i + 14) * 22 + ((i * 17) % 90 - 45)
        series.append(PricePoint(date=d, price=price, is_forecast=False))

    for i in range(0, 15):
        d = today + timedelta(days=i)
        if i <= 5:
            price = base + i * 90
        else:
            price = base + 5 * 90 - (i - 5) * 30
        series.append(
            PricePoint(
                date=d,
                price=price,
                is_forecast=True,
                forecast_low=int(price * 0.92),
                forecast_high=int(price * 1.08),
            )
        )
    return series


@router.get("", response_model=ShippingDashboard)
async def shipping_dashboard(
    crop: str = "tomato",
    region: str = "옥천",
) -> ShippingDashboard:
    """
    출하 도우미 (출하 의사결정).

    TODO 실데이터 연동:
      - price_series 실측: KAMIS 14일 도매가
      - price_series 예측: Prophet 14일 forecast (yhat, yhat_lower, yhat_upper)
      - peer: 스마트팜코리아 우수농가 동일 시점 출하 패턴
      - decision: core.shipping.score.compute() — 가격 추세 + 기상 + 우수농가 결합
    """
    today = date.today()
    series = _mock_series(today)

    decision = ShippingDecision(
        score_today=3,
        score_in_3d=5,
        price_today=4200,
        price_in_3d=4470,
        recommendation="3일 후 출하 권장",
        delta_pct=6.4,
    )

    peer = PeerFarmStat(
        region=region,
        total_farms=12,
        farms_aligned=9,
        note="지난 5년 동일 시점 출하 패턴",
    )

    return ShippingDashboard(
        crop_id=crop,
        crop_name=CROP_NAMES.get(crop, crop),
        region=region,
        updated_at=today,
        decision=decision,
        price_series=series,
        peer=peer,
    )
