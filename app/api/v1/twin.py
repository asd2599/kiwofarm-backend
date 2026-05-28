from fastapi import APIRouter

from app.core.twin.simulator import simulate
from app.schemas.twin import TwinResponse

router = APIRouter(prefix="/twin", tags=["twin"])


@router.get("/{crop_id}", response_model=TwinResponse)
async def get_twin(crop_id: str) -> TwinResponse:
    """주어진 작목의 1년 디지털 트윈 시뮬레이션 결과.

    TODO:
      - 사용자 입력(지역·면적·시설) 반영: query 또는 body로 받기
      - core.twin.simulator 의 결정론적 시뮬+노이즈 결과 연결
    """
    return simulate(crop_id)
