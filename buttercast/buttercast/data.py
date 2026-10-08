import csv
import hashlib
import io
import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path

# Price CSVs ship inside the repo (data/) so a container or Hugging Face Space needs nothing else.
AUDIT = Path(os.environ.get("BUTTERCAST_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
RULES = json.loads((Path(__file__).parent / "rules.json").read_text(encoding="utf-8"))


def check_units(rows):
    """Weekly ratios far outside real history mean mixed units, not market drift."""
    low, high = RULES["data_quality"]["min_weekly_ratio"], RULES["data_quality"]["max_weekly_ratio"]
    for a, b in zip(rows, rows[1:]):
        ratio = b["midpoint"]/a["midpoint"]
        if not low <= ratio <= high:
            raise ValueError(f"{b['date']} 주간 가격 비율 {ratio:.3f}: 단위 혼입 의심({low}~{high} 벗어남)")
    return rows


def parse_weekly_prices(text):
    """Supplied price series only. Origin and actual/synthetic status remain unverified."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not {"week_ending", "butter_price_usd_lb"}.issubset(reader.fieldnames or []):
        raise ValueError("주간 버터 날짜·가격 필수 열 누락")
    rows, seen = [], set()
    for index, row in enumerate(reader, 2):
        try:
            day = date.fromisoformat(row["week_ending"])
            price = float(row["butter_price_usd_lb"])
        except ValueError as error:
            raise ValueError(f"{index}행 날짜/가격 파싱 실패") from error
        if day in seen or not math.isfinite(price) or price <= 0:
            raise ValueError(f"{index}행 중복 날짜 또는 유효하지 않은 가격")
        seen.add(day)
        rows.append(dict(date=day.isoformat(), midpoint=price, unit="USD/lb",
                         interval_days=7, source="team_provided_origin_unverified",
                         synthetic=None, publication_verified=False,
                         available_on=(day+timedelta(days=7)).isoformat(),
                         availability_policy="historical_replay_assumed_observation_plus_7_days"))
    if not rows:
        raise ValueError("주간 가격 행 없음")
    rows.sort(key=lambda row: row["date"])
    if any((date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days != 7
           for a, b in zip(rows, rows[1:])):
        raise ValueError("주간 가격 누락 구간: 보간하지 않습니다")
    return check_units(rows)


def load_eu(path):
    """Real weekly prices; +7 days is an explicit replay assumption, not publication evidence."""
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"observation_date", "butter_eur_per_100kg", "source"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("EU CSV 필수 날짜·가격·출처 열 누락")
        rows, seen = [], set()
        for index, row in enumerate(reader, 2):
            day = date.fromisoformat(row["observation_date"])
            price = float(row["butter_eur_per_100kg"])
            if day in seen or not math.isfinite(price) or price <= 0:
                raise ValueError(f"EU {index}행 중복 날짜 또는 유효하지 않은 가격")
            seen.add(day)
            rows.append(dict(date=day.isoformat(), midpoint=price, unit="EUR/100kg",
                             source=row["source"], synthetic=False, interval_days=7,
                             available_on=(day+timedelta(days=7)).isoformat(),
                             publication_verified=False,
                             availability_policy="historical_replay_assumed_observation_plus_7_days"))
    if not rows:
        raise ValueError("EU CSV 가격 없음")
    rows.sort(key=lambda row: row["date"])
    if any((date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days != 7
           for a, b in zip(rows, rows[1:])):
        raise ValueError("EU 주간 가격에 누락 구간이 있습니다. 보간하지 않습니다.")
    return check_units(rows)


def parse_usda(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    required = {"report_date", "published_date", "price_min", "price_max", "price_Unit", "lot_Desc", "region"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError(f"필수 USDA 열 누락: {sorted(required-set(reader.fieldnames or []))}")
    result, seen = [], set()
    for index, row in enumerate(reader, 2):
        if row["price_Unit"] != "Dollars per Metric Ton":
            raise ValueError(f"{index}행 가격 단위 오류: {row['price_Unit']}")
        if row["region"] != "Oceania" or row["lot_Desc"] != "82% Butterfat":
            raise ValueError(f"{index}행 원산지/규격 오류")
        try:
            day = datetime.strptime(row["report_date"], "%m/%d/%Y").date()
            published = datetime.strptime(row["published_date"], "%m/%d/%Y %H:%M:%S")
            low, high = float(row["price_min"]), float(row["price_max"])
        except ValueError as error:
            raise ValueError(f"{index}행 날짜/가격 파싱 실패: {error}") from error
        if day in seen:
            raise ValueError(f"중복 보고일: {day}")
        if not math.isfinite(low) or not math.isfinite(high) or not 0 < low <= high:
            raise ValueError(f"{index}행 유한 양수 가격 및 하한≤상한 필요")
        if published.date() < day:
            raise ValueError(f"{index}행 보고일보다 발표일이 빠릅니다")
        seen.add(day)
        # Unknown source timezone: conservative availability DATE, never fabricate a UTC timestamp.
        result.append(dict(date=day.isoformat(), published_raw=row["published_date"],
                           available_on=(published.date()+timedelta(days=2)).isoformat(),
                           low=low, high=high, midpoint=(low+high)/2, source="USDA 1099",
                           unit="USD/ton", synthetic=False, availability_policy="publication_date_plus_2_days"))
    if not result:
        raise ValueError("CSV에 가격 행이 없습니다")
    return sorted(result, key=lambda r: r["date"])


def bundled_rows():
    return load_eu(AUDIT / "eu-butter-weekly.csv")


def version(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:12]


def quality(rows, as_of=None):
    as_of = as_of or date.today()
    days = [date.fromisoformat(r["date"]) for r in rows]
    interval = rows[0].get("interval_days", 14)
    gaps = [dict(after=a.isoformat(), before=b.isoformat(), days=(b-a).days)
            for a, b in zip(days, days[1:]) if (b-a).days != interval]
    latest = rows[-1]
    expected = date.fromisoformat(latest["available_on"])+timedelta(days=interval)
    delay = max(0, (as_of-expected).days)
    return dict(rows=len(rows), start=rows[0]["date"], end=latest["date"], gaps=gaps,
                missing_required=0, duplicates=0, missing_rate=0, invalid_units=0,
                next_expected_available=expected.isoformat(), delay_days=delay,
                freshness="stale" if delay else "current", interval_days=interval,
                schedule=f"{interval}일 관측 주기; 실제 발표 일정 미확인",
                availability_note=latest.get("availability_policy"), publication_verified=latest.get("publication_verified", False))

