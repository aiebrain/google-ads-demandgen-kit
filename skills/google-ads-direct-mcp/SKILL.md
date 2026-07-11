---
name: google-ads-direct-mcp
description: >-
  Claude Code를 Google Ads API에 직접 연결하는 원격 MCP 서버를 처음부터 끝까지 세팅하는 튜토리얼.
  개발자 토큰·OAuth·리프레시 토큰 발급 → 쓰기(생성·수정·삭제) 가능한 MCP 서버 → Cloud Run 배포 →
  Claude Code 등록까지 실제로 작동하는 순서를 안내한다. 사용자가 "Google Ads를 Claude에 연결", "구글애즈
  API 직접 연결", "광고 캠페인 만들고 수정하게 해줘", "Google Ads MCP 서버 만들자/배포하자", "여러 PC·팀이
  같은 구글애즈 서버 쓰게", "developer token/refresh token 발급", "Cloud Run에 MCP 올리자" 같은 말을
  하거나, 시중 오픈소스 서버엔 없는 생성·수정 권한이 필요할 때 반드시 이 스킬을 쓴다. 기성 read-only 서버로는
  안 되는, 쓰기 가능한 자체 서버가 필요한 경우를 위한 것이다. Windows·macOS·Linux 지원.
---

# Claude Code ↔ Google Ads API 직접 연결 (튜토리얼)

시중 오픈소스 Google Ads MCP 서버(구글 공식 포함)는 대부분 **읽기 전용**이다. 캠페인을 만들거나
예산·상태를 바꾸려면 API를 직접 물어다 쓰는 **자체 서버**가 필요하다. 이 튜토리얼은 그 서버를
Cloud Run에 올려 어느 컴퓨터의 Claude Code에서도 같은 HTTPS URL로 쓰게 만든다.

```text
Claude Code (어느 PC든)
   → https://<your-service>.run.app/mcp   (Authorization: Bearer <token>)
   → Cloud Run (이 MCP 서버)
   → Google Ads API
비밀값(개발자 토큰·리프레시 토큰·베어러 토큰)은 Secret Manager에만.
```

## 이 튜토리얼로 만들어지는 것 (툴 16개)

- **읽기**: `list_accessible_customers`, `search_google_ads`(GAQL), `campaign_performance`, `customer_children`
- **예산·캠페인**: `create_campaign_budget`, `update_campaign_budget`,
  `create_search_campaign`(기본 **PAUSED**), `update_campaign`, `delete_campaign`
- **광고그룹·키워드·광고**: `create_ad_group`(PAUSED), `update_ad_group`,
  `add_keywords`(배치, EXACT/PHRASE/BROAD), `add_negative_keywords`(캠페인/광고그룹 레벨),
  `create_responsive_search_ad`(RSA, PAUSED)
- **타게팅**: `add_geo_targets`(지역), `add_language_targets`(언어)
- **디멘드젠 애셋**: `upload_image_asset`(URL→이미지), `upload_logo_asset`(URL→로고),
  `attach_youtube_video_asset`(YouTube ID→영상 애셋)
- **디멘드젠 캠페인·광고**: `create_demand_gen_campaign`(PAUSED), `create_demand_gen_ad_group`(PAUSED),
  `create_demand_gen_multi_asset_ad`(이미지 광고, PAUSED), `create_demand_gen_video_responsive_ad`(영상 광고, PAUSED)
- **검수**: `validate_created_demand_gen_ad` — 생성한 광고의 정책 심사 상태(승인/거부/심사중 + 정책 토픽) 확인
  — ✅ 이미지 광고 파이프라인(캠페인→광고그룹→이미지/로고 애셋→멀티애셋 광고→검수)은 **실계정에서 E2E 검증 완료**.
  영상 광고는 구조 검증됨(실제 YouTube 영상 ID로 최종 확인 권장). 전부 PAUSED로 생성됨.
- **안전 설계**: 새 광고그룹·캠페인·광고는 기본 일시중지, ENABLE은 `confirm_enable='ENABLE'`,
  삭제는 `confirm_delete='DELETE'` 필요. 응답에서 토큰류는 자동 마스킹.

즉 예산부터 실제 노출 가능한 검색광고 한 세트(캠페인→광고그룹→키워드→RSA→지역/언어)를
Claude Code 대화만으로 구성할 수 있다. 전체 흐름 예시는 아래 "실제 광고 세팅 흐름" 참고.

## 필요한 자료 (assets/)

이 스킬은 바로 배포 가능한 시작 코드를 번들로 갖고 있다. 새 프로젝트 폴더에 이것들을 복사해서 시작한다:

| 파일 | 역할 |
|------|------|
| `assets/google_ads_mcp_server.py` | MCP 서버 본체 (읽기+안전한 쓰기 툴) |
| `assets/requirements.txt` | 파이썬 의존성 |
| `assets/Dockerfile` | Cloud Run용 컨테이너 |
| `assets/oauth_setup.py` | OAuth 1회 실행 → `google_ads_adc.json`(리프레시 토큰) 생성 |
| `assets/deploy_cloud_run.py` | API 활성화·시크릿 생성·배포를 한 번에 |
| `assets/.env.example` | 설정값 템플릿 |
| `assets/.gcloudignore` | 배포 시 비밀·잡파일 제외 |

문제 해결·함정 모음은 `references/troubleshooting.md` 참고.

---

## Step 0 — 준비물 설치·설정 (여기부터가 진짜 시작)

아래 네 개가 없으면 뒤 단계에서 반드시 막힌다. 하나씩 갖춘 뒤 Step 1로 간다.
이미 다 돼 있으면 각 "완료 기준"만 확인하고 건너뛰어도 된다.

### 0-1. Google Ads 관리자(MCC) 계정

개발자 토큰은 **관리자(MCC) 계정에서만** 발급된다. 일반 광고 계정만 있으면 토큰을 못 받는다.

1. https://ads.google.com/home/tools/manager-accounts 에서 **관리자 계정 만들기**.
2. 실제 광고를 집행하는 계정을 이 MCC 밑에 **연결(link)** 한다(초대 수락).
3. MCC ID(상단 10자리, 하이픈 제거하면 숫자 10자리)를 적어둔다 → 나중에 `GOOGLE_ADS_LOGIN_CUSTOMER_ID`.

**완료 기준**: MCC 계정에 로그인되고, 조작할 광고 계정이 그 아래 연결돼 있다.

### 0-2. Google Cloud 프로젝트 + 결제 연결

Cloud Run·Secret Manager·Google Ads API가 모두 이 프로젝트 위에서 돈다. 결제 연결이 없으면 배포가 막힌다.

1. https://console.cloud.google.com 에서 **새 프로젝트 만들기** → 프로젝트 ID를 적어둔다(예: `my-ads-mcp`).
2. **Billing(결제)** 에서 결제 계정을 이 프로젝트에 연결한다. (Cloud Run은 무료 한도가 넉넉하지만 결제 연결 자체는 필수.)

**완료 기준**: 프로젝트 ID를 확보했고, 그 프로젝트에 결제가 연결돼 있다.

### 0-3. gcloud CLI 설치 + 로그인

배포·시크릿 관리에 쓰는 도구다.

1. 설치: https://cloud.google.com/sdk/docs/install (OS별 안내)
   - Windows: 인스톨러(.exe) 실행. macOS/Linux: 페이지의 설치 스크립트.
2. 설치 후 새 터미널에서:

```bash
gcloud init                         # 로그인 + 프로젝트 선택 (0-2에서 만든 프로젝트)
gcloud auth login                   # 브라우저 인증 (gcloud init에서 이미 했으면 생략)
gcloud config set project <PROJECT_ID>
gcloud config list                  # account 와 project 가 맞는지 확인
```

**완료 기준**: `gcloud config list`에 본인 계정과 올바른 프로젝트가 보인다.

### 0-4. Python 3.10 이상

OAuth 스크립트와 로컬 검증에 필요하다.

```bash
python --version        # 3.10+ 인지 확인 (Windows에서 안 되면 py --version)
```

없거나 낮으면 https://www.python.org/downloads/ 에서 설치. Windows 설치 시
**"Add python.exe to PATH"** 체크를 꼭 켠다.

**완료 기준**: `python --version`이 3.10 이상을 출력한다.

> 💡 개념 정리: **개발자 토큰 ≠ OAuth**. 개발자 토큰은 "이 앱이 Google Ads API를 써도 된다"는
> 허가증(0-1의 MCC에서 발급), OAuth는 "누구의 계정 권한으로" 접근하느냐(Step 2·4). 둘 다 있어야 호출이 된다.

---

## Step 1 — 개발자 토큰 발급

관리자(MCC) 계정 로그인 → **Tools & Settings → Setup → API Center** → 개발자 토큰 확인.
- 처음이면 신청 폼이 뜬다. 발급 직후엔 보통 **Test 등급**(테스트 계정만 호출 가능)일 수 있다.
- 운영 계정에 실제로 호출하려면 **Basic 이상 승인**이 필요하다. 승인 전엔 Step 8에서
  `DEVELOPER_TOKEN_NOT_APPROVED` 에러가 날 수 있으니 미리 신청해 둔다.

**완료 기준**: 22자 개발자 토큰 문자열을 확보했다.

## Step 2 — Google Cloud에서 API 켜고 OAuth 클라이언트 만들기

1. Cloud Console에서 **Google Ads API** 사용 설정(enable).
2. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
3. 없으면 **OAuth consent screen** 먼저 구성(External, 본인 계정을 test user로 추가).
4. Application type = **Desktop app** 선택 → 생성 → **JSON 다운로드**.

**완료 기준**: OAuth 클라이언트 JSON 파일을 내려받았다 (client_id/client_secret 포함).

## Step 3 — 프로젝트 폴더 만들고 설정값 채우기

새 폴더에 `assets/`의 파일들을 복사한다. 설정 디렉터리(기본 `~/.google-ads-mcp`)를 만들고:

- Step 2에서 받은 OAuth 클라이언트 JSON을 그 폴더에 `google_ads_oauth_client.json`으로 저장.
- `.env.example`을 그 폴더에 `.env`로 복사하고 값 채우기(개발자 토큰, 프로젝트 ID, MCC/기본 고객 ID).

```bash
mkdir -p ~/.google-ads-mcp
cp assets/.env.example ~/.google-ads-mcp/.env      # 값 편집
# (다운로드한 OAuth 클라이언트 JSON을 ~/.google-ads-mcp/google_ads_oauth_client.json 로 저장)
```

> Windows에서 `~`는 PowerShell 기준 `$HOME`. 경로를 바꾸고 싶으면 환경변수 `GOOGLE_ADS_MCP_HOME`로 지정.

**완료 기준**: 설정 폴더에 `.env`와 `google_ads_oauth_client.json`이 있다.

## Step 4 — OAuth 동의 → 리프레시 토큰 생성

의존성 설치 후 OAuth 스크립트를 1회 실행한다. 브라우저(또는 출력된 URL)에서 본인 구글 계정으로
동의하면, 서버가 쓸 `google_ads_adc.json`(client_id·client_secret·**refresh_token**)이 만들어진다.

```bash
pip install -r requirements.txt
python oauth_setup.py
```

**완료 기준**: 설정 폴더에 `google_ads_adc.json`이 생성됐다. 이 파일은 **비밀** — 절대 커밋·공유 금지.

## Step 5 — 로컬에서 자격증명 검증 (배포 전에)

배포 전에 자격증명이 실제로 통하는지 확인한다. 서버에 내장된 self-test가 계정 목록을 불러온다:

```bash
python google_ads_mcp_server.py --self-test
```

계정 ID 목록이 나오면 개발자 토큰·OAuth가 정상이다. 에러가 나면 `references/troubleshooting.md`의
"인증 에러"를 먼저 해결하고 진행한다 (배포하면 원인 찾기가 더 어렵다).

**완료 기준**: self-test가 `customers` 목록을 반환한다.

## Step 6 — 원격 접근용 베어러 토큰 만들기

이 서버는 공개 URL로 뜨므로, **베어러 토큰**이 유일한 잠금장치다. 길고 무작위여야 한다:

```bash
# 강한 토큰 생성 (아무 값이나 쓰지 말 것)
python -c "import secrets; print(secrets.token_urlsafe(32))" > mcp_bearer_token.txt
```

이 파일은 `.gcloudignore`로 이미지에서 제외되고, 값은 Secret Manager로만 올라간다.

**완료 기준**: `mcp_bearer_token.txt`에 무작위 토큰이 들어 있다.

## Step 7 — Cloud Run 배포

배포 스크립트가 (1) 필요한 API 활성화 → (2) 시크릿 3개 생성(개발자 토큰/ADC json/베어러 토큰) →
(3) 런타임 서비스계정에 `secretmanager.secretAccessor` 권한 부여 → (4) 계정 ID 환경변수 주입 →
(5) 소스 빌드·배포까지 한 번에 한다. 첫 배포가 깨지는 두 흔한 원인(API 미활성화, 서비스계정
시크릿 접근 권한 누락)을 스크립트가 미리 처리하므로 그대로 실행하면 된다.

```bash
python deploy_cloud_run.py
```

- 리전 기본값은 서울(`asia-northeast3`). 바꾸려면 `REGION=us-central1 python deploy_cloud_run.py`.
- 끝나면 `MCP_URL`과 등록 명령이 출력된다.

만약 배포 후에도 컨테이너가 시크릿을 못 읽어 죽으면(로그에 permission denied), Cloud Run 런타임
서비스계정에 `roles/secretmanager.secretAccessor`를 부여해야 한다 —
`references/troubleshooting.md`의 "시크릿 권한" 참고.

**완료 기준**: HTTPS 서비스 URL이 나오고, `curl https://<url>/healthz`가 `{"ok": true}`를 준다.

## Step 8 — Claude Code에 등록하고 확인

같은 서버를 쓸 **모든 컴퓨터에서** 아래를 실행한다(토큰은 안전한 채널로만 공유):

```bash
claude mcp add --transport http google_ads_direct https://<your-url>/mcp \
  --header "Authorization: Bearer <YOUR_BEARER_TOKEN>"   # mcp_bearer_token.txt 값으로 치환
claude mcp list
```

Claude Code에서 **읽기부터** 확인한다: "내 구글애즈 계정 목록 보여줘"(`list_accessible_customers`),
그다음 최근 성과 조회. 문제없으면 **PAUSED 캠페인 생성**으로 쓰기까지 검증한다(바로 라이브로 켜지 말 것).

**완료 기준**: `claude mcp list`에 `google_ads_direct`가 뜨고, 읽기 호출이 성공한다.

## Step 9 — 안전한 실사용 순서 (쓰기 켜기 전)

1. 읽기 조회로 계정·캠페인 확인.
2. 예산 생성 → 리소스 이름 확인.
3. 캠페인은 **PAUSED로 생성** → UI/조회로 확인.
4. 검증 끝난 뒤에만 `confirm_enable='ENABLE'`로 활성화.
5. 삭제는 `confirm_delete='DELETE'`, 가능하면 삭제 대신 일시중지.

## 팀·타인에게 공유하는 법

- **코드/이 스킬은 공유해도 됨.** 비밀값은 절대 공유 금지(`.env`, `google_ads_adc.json`,
  `mcp_bearer_token.txt`, OAuth 클라이언트 JSON).
- 받는 사람은 **자기 개발자 토큰·자기 OAuth로 Step 1~8을 각자** 수행 → 자기 Cloud Run에 배포.
- 하나의 공용 엔드포인트를 팀이 함께 쓸 거면, **URL과 베어러 토큰만** 안전 채널로 전달한다.
  (단, 그 토큰을 가진 사람은 연결된 광고계정을 조작할 수 있음을 명심 — 토큰은 곧 광고비 권한이다.)

## 실제 광고 세팅 흐름 (Claude Code 대화 예시)

모두 기본 PAUSED로 만들어지므로 안전하게 조립하고, 마지막에 확인 후 켠다:

1. "예산 만들어줘: 하루 5만원" → `create_campaign_budget` (amount_micros=50000000)
2. "그 예산으로 검색 캠페인 생성" → `create_search_campaign` (PAUSED, budget_resource_name 전달)
3. "한국·한국어로 타게팅" → `add_geo_targets`(2410), `add_language_targets`(1012)
4. "광고그룹 만들고 키워드 넣어줘" → `create_ad_group` → `add_keywords`(PHRASE/EXACT)
5. "제외 키워드 추가" → `add_negative_keywords` (캠페인 레벨)
6. "반응형 검색광고 작성" → `create_responsive_search_ad` (헤드라인 3~15, 설명 2~4, final_url)
7. 검수 후 활성화 → `update_campaign`(status='ENABLED', confirm_enable='ENABLE') 및 광고그룹/광고도 동일

## 무엇이 되고 안 되나

되는 것: 계정/캠페인 리포팅, 예산·검색캠페인·광고그룹·키워드(+제외)·RSA 생성/수정, 지역·언어 타게팅,
상태 변경·삭제 — **검색광고 한 세트를 처음부터 끝까지**. 그리고 **이미지 기반 디멘드젠**(이미지 애셋
업로드 → 캠페인 → 광고그룹 → 멀티애셋 광고, 전부 PAUSED)까지.

디멘드젠의 API 제약(반드시 알 것):
- **입찰은 전환/클릭 기반**(수동 CPC 불가). 전환추적 없으면 `MAXIMIZE_CLICKS`로.
- **영상은 유튜브에 먼저 업로드**한 뒤 video ID로 참조(`create_youtube_video_asset`). 생 영상 업로드 불가.
- **이미지는 URL로 전달** → 서버가 받아 애셋 생성(모델에 base64로 넣지 말 것).
- 디멘드젠 5개 툴은 문서 기반 신규 코드이니, 반드시 **PAUSED로 만들어 UI에서 확인 후** 활성화.

아직 없는 것(PMax·디스플레이 캠페인, 디멘드젠 영상/캐러셀 광고 포맷, 사이트링크·콜아웃 확장 애셋,
오디언스·Customer Match, 입찰전략 세부설정 등)은 `google_ads_mcp_server.py`에 **같은 패턴**으로 추가하면
된다 — 파괴적/과금 동작은 `confirm_*` 인자 뒤에, 새 객체는 기본 PAUSED, 반환은 `_json(...)`으로 감쌀 것.
Cloud Run은 서버를 호스팅할 뿐, 없는 기능을 만들어주지 않는다.
