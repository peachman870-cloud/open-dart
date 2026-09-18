"""자료 미리 받아두기 (GitHub Actions가 매일 새벽 실행, PC에서는 prefetch.bat)
sectors.json 의 회사들에 대해 앱이 쓰는 DART 재무·주식수·배당·EPS와 주가를 미리 받아 cache/ 폴더에 저장.
앱은 켤 때 이 파일을 읽어서 서버 조회 없이 바로 화면을 보여줌."""
import datetime as dt
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import dart
import prices

ROOT = Path(__file__).parent
OUT = ROOT / "cache"


def secret(name):
    v = os.environ.get(name, "")
    if not v:
        f = ROOT / ".streamlit" / "secrets.toml"
        if f.exists():
            m = re.search(rf'^{name}\s*=\s*"([^"]*)"', f.read_text(encoding="utf-8"), re.M)
            v = m.group(1) if m else ""
    return v


KEY, GOV, KRX = secret("DART_API_KEY"), secret("DATA_GO_KR_KEY"), secret("KRX_API_KEY")
TODAY = (dt.datetime.utcnow() + dt.timedelta(hours=9)).date()  # 한국시간
Y = TODAY.year
YEARS = range(Y - 4, Y + 1)  # 앱 기본 연도 범위(최근 4년) + 전년 비교용 1년
REPORTS = ("11013", "11012", "11014", "11011")  # 1분기, 반기, 3분기, 사업보고서


def find_companies(corps):
    sectors = json.loads((ROOT / "sectors.json").read_text(encoding="utf-8"))
    norm = lambda x: x.replace(" ", "").replace("(주)", "").lower()
    by_name = dict(zip(corps["corp_name"], zip(corps["corp_code"], corps["stock_code"])))
    by_code = {s: (c, s) for c, s in zip(corps["corp_code"], corps["stock_code"])}
    out = {}
    for names in sectors.values():
        for n in names:
            hits = [by_name[n]] if n in by_name else [by_code[n]] if n in by_code else \
                [v for k, v in by_name.items() if norm(n) in norm(k)]
            for cc, sc in hits:
                out[cc] = sc
    return out


ERRORS = []


def safe(f, *a, **kw):
    try:
        return f(*a, **kw)
    except Exception as e:
        if len(ERRORS) < 30:
            ERRORS.append(f"{getattr(f, '__name__', f)} {a[1:] if a else ''} {kw.get('corp_code', '')}: {e}")
        return None


DONE = [0]
DEADLINE = time.time() + 150 * 60  # 2시간 30분이 지나면 받은 것까지만 저장하고 끝냄


def fetch_company(item):
    if time.time() > DEADLINE:
        return
    cc, sc = item
    safe(dart.company, KEY, cc)
    for y in YEARS:
        for rc in REPORTS:
            for fs in ("CFS", "OFS"):  # 연결, 별도
                safe(dart._get, KEY, "fnlttSinglAcntAll.json", corp_code=cc, bsns_year=str(y), reprt_code=rc, fs_div=fs)
            if y >= Y - 1:
                safe(dart.shares, KEY, cc, y, rc)
        safe(dart.dividend, KEY, cc, y)
    safe(prices.history, sc, 365, GOV)
    DONE[0] += 1
    if DONE[0] % 15 == 0:  # 중간 저장 (시간 초과로 끊겨도 받은 자료는 남음)
        safe(dart.dump_cache, OUT / "dart.json.gz")
        print(f"  {DONE[0]}개 회사 완료", flush=True)


def fetch_krx(dates):
    """분기말·최근 날짜의 KRX 시세 (휴장일이면 직전 거래일까지)"""
    if not KRX:
        return
    for d in dates:
        for i in range(11):
            day = d - dt.timedelta(days=i)
            if day.weekday() >= 5:
                continue
            if not prices._krx_day(day, KRX).empty:
                break


def main():
    if not KEY:
        raise SystemExit("DART_API_KEY 가 없어요")
    OUT.mkdir(exist_ok=True)
    corps = dart.load_corp_codes(KEY)
    corps.to_csv(OUT / "corps.csv", index=False)
    comp = find_companies(corps)
    # 지난번에 받은 자료 재사용: 지난 연도 보고서는 바뀌지 않으므로 다시 받지 않고, 최근 2년만 새로 받음
    n0 = dart.load_cache(OUT / "dart.json.gz", valid_hours=24 * 3650)
    recent = {str(Y), str(Y - 1)}
    for k in [k for k in dart._api_cache if dict(k[1]).get("bsns_year") in recent or k[0] in ("company.json",)]:
        dart._api_cache.pop(k, None)
    print(f"회사 {len(comp)}개 · 연도 {YEARS.start}~{YEARS.stop - 1} · 기존 자료 {n0}건 재사용", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(fetch_company, comp.items()))
    ends = [dt.date(y, m, dd) for y in range(Y - 3, Y + 1) for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31))]
    fetch_krx([d for d in ends if d <= TODAY] + [TODAY - dt.timedelta(days=1), TODAY])
    n1 = dart.dump_cache(OUT / "dart.json.gz")
    n2 = prices.dump_cache(OUT / "prices.json.gz", set(comp.values()))
    msg = f"{TODAY} · 회사 {len(comp)}개 · DART 조회 {n1}건, 주가 {n2} 저장 · {time.time() - t0:.0f}초"
    print(msg)
    (OUT / "prefetch_log.txt").write_text(msg + "\n\n오류 예시:\n" + "\n".join(ERRORS), encoding="utf-8")


if __name__ == "__main__":
    main()
