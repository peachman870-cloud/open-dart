"""정기 리포트 생성 (GitHub Actions가 매일 실행)
watchlist.txt 의 회사들에 대해 최근 공시와 최신 재무를 요약해 reports/ 에 저장."""
import datetime as dt
import os
from pathlib import Path

import dart

ROOT = Path(__file__).parent
KEY = os.environ["DART_API_KEY"]
DAYS = int(os.environ.get("REPORT_DAYS", "1"))


def read_watchlist():
    lines = (ROOT / "watchlist.txt").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def main():
    today = dt.datetime.utcnow() + dt.timedelta(hours=9)  # 한국시간
    today = today.date()
    bgn = today - dt.timedelta(days=DAYS)
    corps = dart.load_corp_codes(KEY)
    out = [f"# 정기 리포트 {today}", f"_공시 기간: {bgn} ~ {today}_", ""]
    for w in read_watchlist():
        m = corps[(corps["stock_code"] == w) | (corps["corp_name"] == w)]
        if m.empty:
            out.append(f"## {w}\n- 회사를 찾지 못했어요 (이름이나 종목코드 확인)\n")
            continue
        row = m.iloc[0]
        out.append(f"## {row.corp_name} ({row.stock_code})")
        df = dart.disclosures(KEY, row.corp_code, bgn.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
        if df.empty:
            out.append("- 새 공시 없음")
        else:
            for _, r in df.iterrows():
                mark = "**[중요]** " if dart.is_important(r["공시명"]) else ""
                out.append(f"- {mark}{r['접수일']} [{r['공시명']}]({r['링크']}) · {r['제출인']}")
        f = dart.financials(KEY, row.corp_code, [today.year - 2, today.year - 1])
        if not f.empty:
            l = f.iloc[-1]
            fmt = lambda v: "-" if v is None or v != v else f"{v:,.1f}"
            out.append(f"- 최근 실적({int(l['연도'])}, {l['기준']}): 매출 {fmt((l['매출액'] or 0) / 1e8)}억 · "
                       f"영업이익률 {fmt(l['영업이익률(%)'])}% · 부채비율 {fmt(l['부채비율(%)'])}% · "
                       f"매출성장률 {fmt(l['매출증가율(%)'])}%")
        out.append("")
    text = "\n".join(out)
    rdir = ROOT / "reports"
    rdir.mkdir(exist_ok=True)
    (rdir / f"{today}.md").write_text(text, encoding="utf-8")
    (rdir / "latest.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
