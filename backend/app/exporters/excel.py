"""CSV/Excel 결과 산출.

상세개발계획.md §6 `GET /api/jobs/{id}/export?format=xlsx|csv`. `results`
테이블 레코드를 pandas DataFrame으로 변환해 openpyxl로 저장한다.
M2 후반~M4에서 구현.
"""

from __future__ import annotations

# TODO(M2/M4): results 테이블 -> pandas.DataFrame -> xlsx/csv 변환 구현
