# 연구용역 알리미

나라장터 용역 입찰공고에서 행정·정책 분야 연구용역을 골라 웹 대시보드로 보여줍니다.
GitHub Actions가 매일 아침 8시와 오후 1시(한국시간)에 자동으로 갱신하므로,
북마크해 둔 주소를 열기만 하면 항상 최신 공고를 볼 수 있습니다.

---

## 처음 설정 (한 번만, 약 15분)

준비물: GitHub 계정, 공공데이터포털 인증키
(인증키가 아직 없다면 [나라장터 입찰공고정보서비스](https://www.data.go.kr/data/15129394/openapi.do)에서 활용신청 후 마이페이지에서 일반 인증키(Decoding)를 복사해 두세요.)

### 1. 저장소 만들기

1. github.com 로그인 → 오른쪽 위 **+** → **New repository**
2. Repository name: `research-bid-alert` (원하는 이름 아무거나)
3. **Public** 선택 (무료 계정에서 웹페이지 발행 조건)
4. **Add a README file** 체크
5. **Create repository** 클릭

### 2. 파일 올리기

**narajangteo.py 와 README.md**
- 저장소 화면에서 **Add file → Upload files** → 두 파일을 끌어다 놓기 → **Commit changes**
- (README.md를 올리면 기존 것을 덮어쓰면서 이 안내가 저장소 첫 화면에 표시됩니다)

**update.yml** — 반드시 정해진 경로에 넣어야 작동합니다
1. **Add file → Create new file**
2. 파일 이름 칸에 `.github/workflows/update.yml` 을 그대로 입력
   (슬래시를 입력하면 폴더가 자동으로 만들어집니다)
3. update.yml 파일을 메모장 등으로 열어 내용 전체를 복사해 붙여넣기
4. **Commit changes**

### 3. 인증키 등록

1. 저장소의 **Settings → Secrets and variables → Actions**
2. **New repository secret** 클릭
3. Name: `SERVICE_KEY` (정확히 이대로)
4. Secret: 공공데이터포털 인증키 붙여넣기 → **Add secret**

키는 여기 한 곳에만 보관됩니다. 공개 저장소이므로 파일 안에 키를 직접 쓰면 안 됩니다.

### 4. 첫 실행

1. 저장소 상단 **Actions** 탭 → 왼쪽 **연구용역 대시보드 갱신** 클릭
2. **Run workflow** 버튼 → 초록색 **Run workflow** 클릭
3. 1~2분 뒤 초록 체크가 뜨면 성공입니다. 실행 항목을 클릭하면
   `https://아이디.github.io/research-bid-alert/` 형태의 주소가 보입니다.
4. 이 주소를 브라우저에 북마크하세요. 휴대폰이라면 홈 화면에 추가해 두면 편합니다.

빨간 X가 뜨면 실행 항목을 눌러 로그를 확인하세요. "등록되지 않은 서비스키"라고
나오면 키를 발급받은 직후라 반영 전일 수 있으니 한두 시간 뒤 다시 실행하면 됩니다.

혹시 주소가 안 열리면 **Settings → Pages**에서 Source가 **GitHub Actions**로
되어 있는지 확인하세요. 보통은 첫 실행 때 자동으로 설정됩니다.

---

## 평소 사용법

- 북마크한 주소를 열면 끝입니다. 매일 아침 8시·오후 1시쯤 자동 갱신됩니다.
- 지금 바로 갱신하고 싶으면: **Actions → Run workflow**
- 키워드 조정: 저장소에서 `narajangteo.py` 클릭 → 연필 아이콘(Edit) →
  상단의 `INCLUDE_KEYWORDS` / `EXCLUDE_KEYWORDS` 수정 → **Commit changes**
  → 다음 갱신부터 반영됩니다.

## 알아두면 좋은 것

- 예약 실행은 GitHub 사정에 따라 몇 분에서 수십 분 늦어질 수 있습니다.
- 저장소에 60일 동안 아무 변경이 없으면 GitHub이 "예약 실행을 멈추겠다"는
  안내 메일을 보냅니다. 메일의 버튼을 누르거나 파일을 한 번만 수정하면
  계속 돌아갑니다.
- 갱신 시각을 바꾸려면 `.github/workflows/update.yml`의 cron 두 줄을 고치면
  됩니다. UTC 기준이라 한국시간에서 9시간을 빼야 합니다.
  (예: 아침 7시로 바꾸려면 `0 22 * * *`)
- 수집 기간(기본 5일), 결과 파일 이름 등은 `narajangteo.py` 상단 설정에서
  바꿀 수 있습니다.

자료 출처: 조달청 나라장터 입찰공고정보서비스(공공데이터포털).
공고명 키워드 기준으로 자동 선별한 결과이므로 누락이 있을 수 있습니다.
