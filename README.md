# 연구용역 알리미

나라장터 용역 입찰공고에서 행정·정책 분야 연구용역을 골라 웹 대시보드로 보여주고,
매일 아침 새 공고를 Gmail로 보내 줍니다. GitHub Actions가 알아서 돌리므로
북마크해 둔 주소를 열거나 메일만 확인하면 됩니다.

- 대시보드: https://changwoong-moon.github.io/research-bid-alert/
- 자동 실행: 매일 **08:10 · 10:10 · 13:10** (한국시간, GitHub 사정으로 몇십 분 늦어질 수 있음)
- 메일: **하루 1통, 아침 첫 성공 실행 때** — 새 공고 목록, 없으면 "오늘 새 공고 없음"

---

## 어떻게 움직이나 (2026-08-16 개편)

1. 선별한 공고를 `data/bids.json`에 **누적 저장**합니다.
2. 매 실행은 **최근 3일치만** 새로 받아 저장분에 합칩니다.
   (수집을 며칠 못 했으면 빠진 기간만큼 자동으로 더 받습니다.)
3. 나라장터 API가 응답하지 않아도 저장분으로 대시보드를 만들고,
   상단에 **"갱신 실패" 배너**를 띄웁니다. 대시보드가 통째로 멈추지 않습니다.
4. 메일은 하루 1통이 원칙입니다.
   - 아침(08:10) 실행이 성공하면 그때 발송. 실패하면 10:10 → 13:10 실행에서 성공하는 순간 발송.
   - 새 공고가 없는 날은 "오늘 새 공고 없음" 메일 (마감 임박 공고가 있으면 함께).
   - 정오까지 수집이 계속 실패하면 "수집 실패" 알림 1통.
   - 같은 날 나중에 올라온 공고는 다음 날 아침 메일에 들어갑니다.

왜 이렇게 바꿨나: 나라장터 API가 오전에 자주 느려지거나 응답을 안 해서,
매번 20일치를 통째로 받던 예전 방식은 아침 갱신이 4번 중 3번 실패했습니다.

---

## 처음 설정 (한 번만, 약 15분)

준비물: GitHub 계정, 공공데이터포털 인증키
(인증키가 아직 없다면 [나라장터 입찰공고정보서비스](https://www.data.go.kr/data/15129394/openapi.do)에서 활용신청 후 마이페이지에서 일반 인증키(Decoding)를 복사해 두세요.)

### 1. 저장소 만들기

1. github.com 로그인 → 오른쪽 위 **+** → **New repository**
2. Repository name: `research-bid-alert` (원하는 이름 아무거나)
3. **Public** 선택 (무료 계정에서 웹페이지 발행 조건)
4. **Add a README file** 체크 → **Create repository**

### 2. 파일 올리기

- 저장소 화면에서 **Add file → Upload files** → `narajangteo.py`, `notify.py`, `README.md`, `.gitignore` 올리기 → **Commit changes**
- `update.yml`은 반드시 정해진 경로에: **Add file → Create new file** → 파일 이름 칸에
  `.github/workflows/update.yml` 입력 → 내용 붙여넣기 → **Commit changes**

### 3. 시크릿 등록

**Settings → Secrets and variables → Actions → New repository secret** 으로 세 개:

| Name | 값 |
|---|---|
| `SERVICE_KEY` | 공공데이터포털 일반 인증키(Decoding) |
| `GMAIL_ADDRESS` | 보내고 받을 Gmail 주소 (예: 본인 Gmail) |
| `GMAIL_APP_PASSWORD` | Google 앱 비밀번호 16자리 — https://myaccount.google.com/apppasswords (2단계 인증 필요) |

공개 저장소이므로 키·비밀번호를 파일 안에 직접 쓰면 안 됩니다. 시크릿에만 둡니다.

### 4. 첫 실행

1. **Actions** 탭 → 왼쪽 **연구용역 알리미 갱신** → **Run workflow** → 초록 **Run workflow**
2. 첫 실행은 20일치를 다 받으므로 3~5분 걸립니다. 초록 체크가 뜨면 성공.
   실행 항목을 클릭하면 `https://아이디.github.io/research-bid-alert/` 주소가 보입니다.
3. 첫 실행 메일: 이전에 안내한 기록(`data/seen.json`)이 없으면 기준선만 저장하고 메일은 안 보냅니다.
   발송 설정을 바로 확인하고 싶으면 **force_mail** 체크를 켜고 한 번 더 실행하세요 (테스트 메일).
4. 주소를 브라우저에 북마크하세요. 휴대폰이라면 홈 화면에 추가하면 편합니다.

빨간 X가 뜨면 실행 항목을 눌러 로그를 확인하세요. "등록되지 않은 서비스키"라고
나오면 키 발급 직후라 반영 전일 수 있으니 한두 시간 뒤 다시 실행하면 됩니다.
주소가 안 열리면 **Settings → Pages**에서 Source가 **GitHub Actions**인지 확인하세요.

---

## 평소 사용법

- 대시보드 주소를 열면 끝입니다. 상단의 "○○ 수집" 시각이 데이터 기준 시각입니다.
  주황색 배너가 보이면 그 회차 수집이 실패해 저장분을 보여주고 있다는 뜻입니다(다음 회차에 자동 재시도).
- 지금 바로 갱신: **Actions → 연구용역 알리미 갱신 → Run workflow**
- Run workflow의 선택 사항
  - **full_refresh**: 최근 20일치를 전량 다시 받습니다. **키워드를 추가한 뒤 한 번** 켜세요
    (평소엔 3일치만 받으므로, 새 키워드에 걸리는 옛 공고는 이걸 켜야 들어옵니다. 제외 키워드 변경은 바로 반영됩니다).
  - **force_mail**: 새 공고가 없어도 테스트 메일을 보냅니다.
- 키워드 조정: `narajangteo.py` → 연필 아이콘(Edit) → 상단 `INCLUDE_KEYWORDS` / `EXCLUDE_KEYWORDS` /
  `EXCLUDE_EXCEPTIONS`(제외 키워드의 예외, 예: "청소"는 빼되 "청소년"은 살림) 수정 → **Commit changes**
  → 다음 갱신부터 반영 (추가한 키워드는 full_refresh 한 번).

## 파일 구성

| 파일 | 역할 |
|---|---|
| `narajangteo.py` | 공고 수집·저장분 병합·대시보드 HTML 생성 (설정은 파일 상단) |
| `notify.py` | 저장분에서 새 공고를 골라 Gmail 발송 (하루 1통 규칙) |
| `.github/workflows/update.yml` | 자동 실행 스케줄 (수집 → 메일 → 기록 저장 → 발행) |
| `data/bids.json` | 선별 공고 누적 저장분 (자동 관리, 20일 지난 공고는 삭제) |
| `data/seen.json` | 메일로 안내한 공고 기록 |
| `data/mail_state.json` | 마지막 안내 메일 날짜 등 |

## 알아두면 좋은 것

- 예약 실행은 GitHub 사정에 따라 몇 분에서 한 시간 넘게 늦어질 수 있습니다.
- 저장소에 60일 동안 아무 변경이 없으면 GitHub이 "예약 실행을 멈추겠다"는 안내 메일을 보냅니다.
  (이 저장소는 매일 data/ 를 커밋하므로 보통 해당되지 않습니다.)
- 실행 시각 변경: `.github/workflows/update.yml`의 cron (UTC 기준, 한국시간 −9시간).
- 수집 기간(`DAYS_BACK`), 증분 기간(`FETCH_DAYS`), 메일 시각 규칙(`notify.py` 상단) 등은 파일 상단 설정에서 바꿉니다.
- 로컬에서 모양만 보려면 `python narajangteo.py --demo` (키 불필요), 메일 내용만 보려면 `python notify.py --dry-run`.

자료 출처: 조달청 나라장터 입찰공고정보서비스(공공데이터포털).
공고명 키워드 기준으로 자동 선별한 결과이므로 누락이 있을 수 있습니다.
