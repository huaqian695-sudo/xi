"""天气预取：每天 07:00 把常看城市的今明天气存进事实库。

为什么预取：天气是最常被问的，临时查一次要 2–3 秒、还容易失败。
早上一次查好，白天她直接读库，秒回。

存哪：`4-facts`（事实块）。它本来就是"带来源、带保质期的结论"，天气正合适。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import timedelta
from typing import Any

from core import clock

# 常看的城市：预取这几个。其它城市现查（geocoding）。
CITIES: dict[str, tuple[float, float]] = {
    "洛阳": (34.6197, 112.4540),
    "北京": (39.9042, 116.4074),
    "安阳": (36.0997, 114.3931),
}

# WMO 天气代码 → 人话
WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    77: "雪粒",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
UA = "xi-project/0.3 (+weather prefetch)"


def _get(url: str, timeout: float = 20) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode(city: str) -> tuple[float, float] | None:
    """城市名 → 经纬度。内置表优先，避免多一次请求。"""
    if city in CITIES:
        return CITIES[city]
    url = f"{GEOCODE_URL}?name={urllib.parse.quote(city)}&count=1&language=zh&format=json"
    try:
        data = _get(url)
    except Exception:  # noqa: BLE001
        return None
    results = data.get("results") or []
    if not results:
        return None
    return float(results[0]["latitude"]), float(results[0]["longitude"])


def fetch_forecast(city: str, days: int = 2) -> list[dict[str, Any]]:
    """查一个城市未来 N 天的天气。返回按天排好的列表。"""
    coord = geocode(city)
    if coord is None:
        return []
    lat, lon = coord
    url = (
        f"{FORECAST_URL}?latitude={lat}&longitude={lon}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max"
        f"&timezone=Asia%2FShanghai&forecast_days={max(1, min(days, 7))}"
    )
    try:
        data = _get(url)
    except Exception:  # noqa: BLE001
        return []

    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    probs = daily.get("precipitation_probability_max") or []

    out = []
    for i, date in enumerate(dates):
        code = int(codes[i]) if i < len(codes) and codes[i] is not None else -1
        high = highs[i] if i < len(highs) else None
        low = lows[i] if i < len(lows) else None
        prob = probs[i] if i < len(probs) else None
        out.append(
            {
                "city": city,
                "date": date,
                "weather": WMO.get(code, f"代码 {code}"),
                "high": high,
                "low": low,
                "rain_prob": prob,
            }
        )
    return out


def statement(item: dict[str, Any]) -> str:
    """一句人话，存库和给模型看都用它。"""
    low, high = item.get("low"), item.get("high")
    temp = f"{low}~{high}℃" if low is not None and high is not None else ""
    prob = item.get("rain_prob")
    rain = f"，降水概率 {prob}%" if prob is not None else ""
    return f"{item['city']} {item['date']} {item['weather']}{'，' + temp if temp else ''}{rain}"


def save(cfg, items: list[dict[str, Any]], persona: str = "xi") -> int:
    """写进事实库。同城同日重新预取时，旧的自然被替换（同源 supersede）。"""
    from core import repo

    saved = 0
    for item in items:
        try:
            # 保质期到目标日期的次日零点
            expired = f"{item['date']}T23:59:00+08:00"
            repo.put_facts(
                cfg,
                question=f"天气:{item['city']}:{item['date']}",
                conversation_key=f"weather:{item['city']}",
                statements=[
                    {
                        "statement": statement(item),
                        "source": "open-meteo",
                        "confidence": 0.9,
                    }
                ],
                ttl_hours=0,  # 下面用 expired_at 覆盖
                persona=persona,
            )
            saved += 1
            _fix_expiry(cfg, item["city"], item["date"], expired, persona)
        except Exception:  # noqa: BLE001 - 一个城市失败不影响别家
            continue
    return saved


def _fix_expiry(cfg, city: str, date: str, expired_at: str, persona: str) -> None:
    """put_facts 算不出"到某天结束"，这里补一刀。"""
    import sqlite3

    from core import db, repo

    key = repo.question_key(f"天气:{city}:{date}")
    path = db.db_path(cfg, "facts", persona)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE facts SET expired_at=? WHERE question_key=? AND superseded_by IS NULL",
            (expired_at, key),
        )
        conn.commit()
    finally:
        conn.close()


def prefetch(cfg, cities: list[str] | None = None, persona: str = "xi") -> int:
    """查一遍常看城市，写库。返回写了几条。"""
    cities = cities or list(CITIES)
    total = 0
    for city in cities:
        items = fetch_forecast(city, days=2)
        total += save(cfg, items, persona)
    return total


async def daily_loop(
    cfg,
    at: str = "07:00",
    cities: list[str] | None = None,
    persona: str = "xi",
    raw=None,
) -> None:
    """每天到点预取一次；启动时先补一次（别让第一问空手）。"""
    import asyncio

    try:
        n = prefetch(cfg, cities, persona)
        if raw is not None:
            raw.append(
                "events",
                {"ts": clock.iso(), "kind": "connect", "detail": f"天气预取（启动）：{n} 条"},
            )
    except Exception as exc:  # noqa: BLE001
        if raw is not None:
            raw.append(
                "events",
                {"ts": clock.iso(), "kind": "error", "detail": f"天气预取失败：{exc}"},
            )

    hour, minute = (int(x) for x in str(at or "07:00").split(":")[:2])
    while True:
        now = clock.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        await asyncio.sleep(max(30, (target - now).total_seconds()))
        try:
            n = prefetch(cfg, cities, persona)
            if raw is not None:
                raw.append(
                    "events",
                    {"ts": clock.iso(), "kind": "connect", "detail": f"天气预取：{n} 条"},
                )
        except Exception as exc:  # noqa: BLE001
            if raw is not None:
                raw.append(
                    "events",
                    {"ts": clock.iso(), "kind": "error", "detail": f"天气预取失败：{exc}"},
                )
