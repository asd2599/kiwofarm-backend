from pydantic import BaseModel


class CropOption(BaseModel):
    """KAMIS 부류·품목·품종 코드 단위 검색 결과."""

    group_code: str
    group_name: str
    item_code: str
    item_name: str
    kind_code: str
    kind_name: str
    label: str
