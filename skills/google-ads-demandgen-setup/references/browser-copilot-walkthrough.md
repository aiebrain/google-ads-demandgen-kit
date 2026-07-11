# 브라우저 코파일럿 실행 명세 (에이전트용)

이 문서는 **Claude(에이전트)가 직접 따라 실행**하는 절차다. 디멘드젠 셋업의 "사람이 직접 해야 하는 웹
단계"를, **Claude Code 내장 브라우저를 옆에 띄우고 실시간으로 안내·검증**하며 진행한다. 사용자가
"브라우저 켜서 같이 진행하자 / 옆에서 가이드해줘" 라고 하면 이 방식을 쓴다.

## 사용하는 도구
- `mcp__Claude_Browser__navigate` — 페이지 이동
- `mcp__Claude_Browser__read_page` (filter: interactive/all) — 접근성 트리로 요소·ref 파악 (스크린샷보다 우선)
- `mcp__Claude_Browser__computer` — screenshot / left_click(ref 또는 coordinate) / type / scroll / zoom
- `mcp__Claude_Browser__form_input` — 폼 값 채우기 (ref 기준)
- `mcp__Claude_Browser__find` — 마지막 read_page에서 요소 검색
- 내장 브라우저 세션이 비어있으면 먼저 `accounts.google.com`으로 이동해 **사용자가 직접 로그인**하게 한다.

## 절대 하지 않는 것 (사용자에게 넘김)
보안·안전 규칙상 아래는 **에이전트가 절대 수행하지 않고**, 화면 위치를 짚어준 뒤 사용자가 직접 하게 한다:
- 비밀번호/로그인 입력, 2단계 인증
- reCAPTCHA("로봇이 아닙니다"·그림 퀴즈)
- 카드/결제 정보 입력
- OAuth "허용"·약관/정책 **동의 체크**·최종 **제출/Submit** 클릭
- 파일 업로드(내장 브라우저는 업로드 툴 없음 → 사용자가 첨부)

토큰·비밀번호·카드가 화면에 뜨는 구간은 **스크린샷을 찍지 않는다**(값은 read_page 텍스트로만 읽어 .env에 저장).

## 역할 패턴 (매 단계 공통)
1. 에이전트: 정확한 URL로 navigate → read_page/screenshot로 현재 화면 파악
2. 에이전트: 채울 수 있는 **비민감 필드**(이름·URL·설명·프로젝트값)는 form_input/type로 채움
3. 에이전트: 사용자가 할 것(로그인·캡차·카드·동의·제출)을 **화면 기준으로 지목**하고 대기
4. 에이전트: 결과를 read_page로 **검증**(예: MCC ID 10자리 확인) → `~/.google-ads-mcp/.env`에 반영
5. 값이 비밀(토큰·리프레시)이면 채팅에 노출하지 말고 파일에만 저장

## 단계별 플레이북

### STEP 1 — 관리자(MCC) 계정
- navigate: `https://ads.google.com/home/tools/manager-accounts` → "지금 시작하기"(sf=manager) 또는 "관리자 계정 만들기"
- 에이전트: 계정 표시 이름 form_input. **국가=대한민국/시간대=(GMT+9)서울/통화=KRW** 가 맞는지 read로 확인(다르면 제출 전 교정 안내).
- 사용자: reCAPTCHA + 제출
- 검증: 계정 전환기(헤더)에서 **10자리 MCC ID** 읽어 확정 → `.env` `GOOGLE_ADS_LOGIN_CUSTOMER_ID`(숫자만)

### STEP 1.5 — MCC 아래 광고계정 (실제 광고 나갈 곳)
- navigate: `https://ads.google.com/aw/accounts` → "+" → "새 계정 만들기"
- ⚠️ **실측 한계**: 이 흐름의 "본인 인증"이 내장 브라우저에서 팝업이 안 열려 "다시 시도" 루프에 빠지는 경우가 있다.
  그때는 **사용자 시스템 크롬**에서 만들라고 안내하고, 생성된 10자리 계정 ID만 받아 `.env` `GOOGLE_ADS_CUSTOMER_ID`에 반영.

### STEP 2 — 개발자 토큰
- navigate: `https://ads.google.com/aw/apicenter`
- 에이전트: 회사명·URL·사용목적 등 비민감 폼 안내(개인정보는 사용자 입력). 약관 동의·"토큰 생성"은 사용자.
- 검증: "토큰 보기"로 22자 토큰 확인(스크린샷 금지) → `.env` `GOOGLE_ADS_DEVELOPER_TOKEN`. 처음은 Test 등급.
- Basic 신청: "액세스 수준 → 기본 액세스 신청" → 영어 신청서 폼. 프로젝트ID·MCC·연락처·URL·설명은 에이전트가 채우고,
  **설계문서 첨부·정책 동의·Submit은 사용자**. (설계문서는 basic-access 템플릿 값만 교체)
- **신청 후 브랜드 인증(Brand Verification)** — 접수 확인 메일이 오고, 완료하면 심사가 빨라짐(Basic 신청자 사실상 필수).
  **자동화 불가, 사용자가 직접**: 개발자토큰↔GCP프로젝트 연결 → OAuth 앱 **외부+프로덕션 게시** → 브랜딩(앱이름·로고·도메인)
  입력 → **"브랜딩 확인" → "브랜딩 게시"**. 에이전트는 GCP 콘솔 브랜딩 페이지로 안내만 하고 각 확정 클릭은 사용자.
  **모든 소통은 이메일**(신청서 연락처)로 오니 계속 확인하라고 안내. (프로덕션 게시는 리프레시 토큰 7일 만료도 해소)

### STEP 3 — GCP (프로젝트·API는 터미널 자동, 카드만 브라우저)
- 프로젝트 생성·Ads API 활성화는 gcloud로 자동(`gcloud projects create`, `gcloud services enable googleads.googleapis.com`).
  단 gcloud가 다른 계정이면 `gcloud auth login <이메일>`(브라우저 열리는 기본 방식; 시스템 브라우저가 열림).
- 결제 카드: navigate `https://console.cloud.google.com/billing/create` → **카드 입력·약관 동의·계속은 사용자**.
- ⚠️ **실측**: 결제 활성화가 최대 24h. `gcloud billing accounts list`에서 OPEN=True 될 때까지 배포 보류.

### STEP 4 — OAuth 동의화면 + 테스트사용자 + 클라이언트 (새 "Google 인증 플랫폼" UI)
- 동의화면: navigate `https://console.cloud.google.com/auth/overview/create?project=<PID>`
  4단계 마법사 — 에이전트가 앱이름/지원이메일 채움, **대상=외부(External)** 라디오 선택, 연락처 이메일 채움.
  마지막 **정책 동의 체크 + 만들기**는 사용자.
- 테스트사용자: navigate `https://console.cloud.google.com/auth/audience?project=<PID>` → "사용자 추가" → 본인 이메일 입력 → 저장.
  (누락 시 로그인에서 403 발생 — 반드시 추가)
- 클라이언트: navigate `https://console.cloud.google.com/auth/clients/create?project=<PID>` → 유형 **데스크톱 앱** 선택.
  ⚠️ **실측 한계**: "만들기" 버튼 자동 클릭이 안 먹히는 경우가 있다 → **사용자가 "만들기" + "JSON 다운로드"** 하게 안내.
  받은 JSON 경로를 받아 `~/.google-ads-mcp/google_ads_oauth_client.json`으로 복사(최상위 키 `installed` 확인).

### STEP 5 — OAuth 동의로 리프레시 토큰
- 실행: `python -u oauth_setup.py` (반드시 `-u`, 아니면 출력 버퍼링으로 URL이 안 보임). 백그라운드로 띄우고 출력에서 동의 URL 확보.
- navigate: 그 동의 URL을 내장 브라우저로 → 사용자: 계정 선택 → "앱 미확인" 경고면 **고급 → (안전하지 않음)으로 이동** → **허용**.
- 로컬 localhost 리다이렉트를 스크립트가 캡처 → `google_ads_adc.json` 생성. read/파일로 refresh_token 존재 검증.

### STEP 6~9 — 자동 (브라우저 거의 없음)
- self-test: `python google_ads_mcp_server.py --self-test` (계정 목록 나오면 자격증명 OK)
- 배포: `python deploy_cloud_run.py`(결제 활성화 후) → MCP URL + 베어러 토큰이 connection.json에
- 등록: `claude mcp add --transport http google_ads_direct <url>/mcp --header "Authorization: Bearer <token>"`
- 검증: `python verify_demandgen.py`

## .env에 모으는 값 (요약)
`GOOGLE_ADS_DEVELOPER_TOKEN`(비밀), `GOOGLE_ADS_LOGIN_CUSTOMER_ID`(MCC), `GOOGLE_ADS_CUSTOMER_ID`(광고계정),
`GOOGLE_PROJECT_ID`. 위치: `~/.google-ads-mcp/.env`. 비밀 파일은 절대 커밋/공유/스크린샷 금지.

## 진행 원칙
- 매 단계 끝에 **검증 후** 다음으로. 사용자에게 넘길 땐 "지금 브라우저에서 X 하세요 → 끝나면 알려주세요"로 명확히.
- 내장 브라우저가 막히면(위 ⚠️ 지점) 즉시 **시스템 크롬 대안**으로 전환 안내(계정은 동일 유지).
