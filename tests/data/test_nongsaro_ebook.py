"""nongsaro_ebook 클라이언트 XML 파서 / resultCode 분기 단위 테스트.

네트워크/DB 없이 _parse_items 만 검증.
"""

from __future__ import annotations

import pytest

from app.data.nongsaro_ebook import NongsaroApiError, _parse_items

LIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <resultCode>00</resultCode>
    <resultMsg>OK</resultMsg>
  </header>
  <body>
    <items>
      <item>
        <mainCategoryCode>CT001</mainCategoryCode>
        <mainCategoryNm>식량작물</mainCategoryNm>
      </item>
      <item>
        <mainCategoryCode>CT002</mainCategoryCode>
        <mainCategoryNm>채소</mainCategoryNm>
      </item>
    </items>
  </body>
</response>
"""

SINGLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>
  <body>
    <item>
      <ebookCode>E100</ebookCode>
      <ebookName>벼 재배</ebookName>
    </item>
  </body>
</response>
"""

ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <resultCode>11</resultCode>
    <resultMsg>인증키 오류</resultMsg>
  </header>
  <body/>
</response>
"""


def test_parse_items_list_form():
    rows = _parse_items(LIST_XML, "mainCategoryList")
    assert rows == [
        {"mainCategoryCode": "CT001", "mainCategoryNm": "식량작물"},
        {"mainCategoryCode": "CT002", "mainCategoryNm": "채소"},
    ]


def test_parse_items_single_form():
    rows = _parse_items(SINGLE_XML, "ebookList")
    assert rows == [{"ebookCode": "E100", "ebookName": "벼 재배"}]


def test_parse_items_raises_on_error_code():
    with pytest.raises(NongsaroApiError) as exc:
        _parse_items(ERROR_XML, "mainCategoryList")
    assert exc.value.code == "11"
    assert exc.value.operation == "mainCategoryList"
