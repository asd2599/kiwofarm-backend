"""도매가 시계열 예측.

기획서: Prophet 기반 예측. 데이터가 짧거나 Prophet 실패 시 선형추세(통계) 폴백.
입력은 (날짜, 가격) 시계열(③ periodProductList 의 '평균' county), 출력은 향후 N일
예측 + 신뢰구간.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

logging.getLogger("prophet").setLevel(logging.ERROR)
logging.getLogger("cmdstanpy").setLevel(logging.ERROR)

_MIN_FOR_PROPHET = 12


@dataclass
class ForecastPoint:
    date: date
    yhat: int
    lower: int
    upper: int


def _linear_forecast(series: list[tuple[date, int]], horizon: int) -> list[ForecastPoint]:
    import numpy as np

    ys = [p for _, p in series]
    n = len(ys)
    x = np.arange(n)
    if n >= 2:
        slope, intercept = np.polyfit(x, ys, 1)
        # 추세 제거 후 잔차 표준편차로 밴드 추정
        fitted = intercept + slope * x
        resid = float(np.std(ys - fitted))
    else:
        slope, intercept, resid = 0.0, float(ys[-1]), ys[-1] * 0.05
    band = max(int(resid * 1.28), int(ys[-1] * 0.03), 100)  # ~80% 구간 근사

    last_date = series[-1][0]
    out: list[ForecastPoint] = []
    for i in range(1, horizon + 1):
        yhat = int(intercept + slope * (n - 1 + i))
        yhat = max(yhat, 0)
        out.append(ForecastPoint(last_date + timedelta(days=i), yhat, max(yhat - band, 0), yhat + band))
    return out


def _prophet_forecast(series: list[tuple[date, int]], horizon: int) -> list[ForecastPoint]:
    import pandas as pd
    from prophet import Prophet

    df = pd.DataFrame({"ds": [d for d, _ in series], "y": [p for _, p in series]})
    model = Prophet(
        growth="linear",
        daily_seasonality=False,
        weekly_seasonality=False,
        yearly_seasonality=False,
        interval_width=0.8,
    )
    model.fit(df)
    future = model.make_future_dataframe(periods=horizon)
    fc = model.predict(future).tail(horizon)
    out: list[ForecastPoint] = []
    for row in fc.itertuples():
        out.append(
            ForecastPoint(
                date=row.ds.date(),
                yhat=max(int(row.yhat), 0),
                lower=max(int(row.yhat_lower), 0),
                upper=max(int(row.yhat_upper), 0),
            )
        )
    return out


_cache: dict[tuple, tuple[list[ForecastPoint], str]] = {}


def forecast_prices(
    series: list[tuple[date, int]], horizon_days: int = 7
) -> tuple[list[ForecastPoint], str]:
    """(예측포인트, method) 반환. method = 'prophet' | 'linear' | 'none'.

    같은 시계열·horizon 은 캐시 — advice/forecast 두 엔드포인트가 Prophet 을
    중복 fit 하지 않도록 한다.
    """
    if len(series) < 3:
        return [], "none"

    key = (len(series), series[0][0].isoformat(), series[-1][0].isoformat(), series[-1][1], horizon_days)
    if key in _cache:
        return _cache[key]

    if len(series) >= _MIN_FOR_PROPHET:
        try:
            result = (_prophet_forecast(series, horizon_days), "prophet")
            _cache[key] = result
            return result
        except Exception:
            pass
    result = (_linear_forecast(series, horizon_days), "linear")
    _cache[key] = result
    return result
