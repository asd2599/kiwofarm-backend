from fastapi import APIRouter, Query

from app.data import kamis_crops
from app.schemas.crops import CropOption

router = APIRouter(prefix="/crops", tags=["crops"])


def _to_option(row: kamis_crops.CropRecord) -> CropOption:
    return CropOption(
        group_code=row["groupCode"],
        group_name=row["groupName"],
        item_code=row["itemCode"],
        item_name=row["itemName"],
        kind_code=row["kindCode"],
        kind_name=row["kindName"],
        label=row["label"],
    )


@router.get("/search", response_model=list[CropOption])
async def search_crops(
    q: str = Query(..., min_length=1, description="품목/품종/부류명 부분일치"),
    limit: int = Query(10, ge=1, le=50),
) -> list[CropOption]:
    return [_to_option(r) for r in kamis_crops.search(q, limit=limit)]
