# 상장회사 분석 웹 앱

DART(전자공시) 데이터로 상장회사의 재무·공시를 보는 웹 앱입니다.

## 기능
- **재무 분석 (전체 재무제표 기준)**: 수익성(매출총이익률·영업이익률·ROE·ROA·EBITDA마진), 안정성(부채비율·유동/당좌비율·차입금의존도·이자보상배율), 활동성(재고자산·매출채권·총자산 회전율/회전일수), 성장성, 현금흐름(FCF·CAPEX)
- **주가·가치평가**: 주가 차트, 시가총액, PER, PBR, PSR, EV/EBITDA, 배당수익률
  - 주가 출처: 공공데이터포털 [금융위원회_주식시세정보](https://www.data.go.kr/data/15094808/openapi.do) (공식, 키 등록 시) → 없으면 네이버 금융(비공식)
- **회사 비교**: 여러 회사를 한 차트로 비교
- **공시**: 기간별 공시 목록, 중요 공시 표시(증자·전환사채·최대주주·소송 등), 원문 링크
- **엑셀 받기**: 왼쪽 메뉴 버튼으로 현재 화면 데이터 다운로드
- **정기 리포트**: 매일 오전 8시 관심 회사 공시 요약 자동 생성 (`watchlist.txt`에서 회사 수정)

## 인터넷에 올리는 방법 (무료, 약 15분)

### 1. DART 인증키 받기
[OpenDART](https://opendart.fss.or.kr) → 회원가입 → 인증키 신청

### 2. GitHub에 올리기
1. [GitHub](https://github.com) 가입 → 새 저장소(New repository) 생성 (Private 추천)
2. **Add file → Upload files** 로 이 폴더의 파일을 모두 올리기
   (`.github` 폴더가 안 보이면 숨김 파일 표시를 켜고 올리기)

### 3. 정기 리포트 켜기 (GitHub Actions)
1. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
2. 이름 `DART_API_KEY`, 값에 인증키 입력
3. **Actions** 탭 → "DART 정기 리포트" → **Run workflow** 로 바로 테스트 가능

### 4. 웹 앱 배포 (Streamlit Community Cloud)
1. [Streamlit Community Cloud](https://share.streamlit.io) 에 GitHub 계정으로 로그인
2. **Create app** → 저장소 선택 → Main file: `app.py`
3. **Advanced settings → Secrets** 에 아래 입력 후 Deploy
   ```
   DART_API_KEY = "인증키"
   DATA_GO_KR_KEY = "공공데이터포털 인증키"   # 선택
   KRX_API_KEY = "KRX OpenAPI 인증키"         # 선택 (PER용 KRX 종가)
   ```
4. 나온 주소(`https://....streamlit.app`)로 PC·휴대폰 어디서나 접속

## 참고
- 금액 단위는 억원, 연결재무제표 우선(없으면 별도).
- 재무 데이터는 DART 주요계정 API(`fnlttSinglAcnt`) 기준이며, 2015년 이후 자료만 제공됩니다.
- 무료 Streamlit 앱은 한동안 안 쓰면 잠들고, 첫 접속 때 깨우는 데 시간이 조금 걸립니다.
- 정기 리포트는 GitHub가 실행하므로 앱이 잠들어도 만들어집니다.
- DART API는 하루 호출 한도가 있습니다 ([공식 안내](https://opendart.fss.or.kr/intro/main.do) 확인).
- 본 앱은 투자 권유가 아닌 참고용입니다.
