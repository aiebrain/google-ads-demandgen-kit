---
name: google-ads-demandgen-setup
description: >-
  Google Ads 디멘드젠(Demand Gen) 광고를 설정할 수 있는 환경을 자동화 스크립트로 구축하고, 다른 PC에서도
  동일하게 재현되게 만드는 스킬. OAuth(일반 Gmail 기준) 인증 → Cloud Run에 디멘드젠 가능한 MCP 서버 배포 →
  Claude Code 등록 → "디멘드젠 설정 가능" 자동 검증까지 원커맨드로 진행한다. 막히는 지점(개발자 토큰 미승인,
  결제 미연결, 권한 오류 등)은 에러를 진단해 해결책을 제시한다. 사용자가 "디멘드젠 광고 설정 가능하게 셋팅",
  "디맨드젠 광고 만들 수 있게 환경 구성", "다른 PC에서도 구글애즈 디멘드젠 되게", "구글애즈 셋업 자동화 스크립트",
  "새 PC에 구글애즈 연결", "디멘드젠 준비됐는지 검증", "옆에 브라우저 켜서 같이 셋업 진행/가이드",
  "직접 해야 하는 웹 단계 안내해줘" 같은 말을 하면 이 스킬을 쓴다. 수동 웹 단계는 내장 브라우저로
  Claude가 옆에서 클릭 단위 안내·검증하는 코파일럿 모드로 진행 가능(references/browser-copilot-walkthrough.md).
  또한 "지금 디멘드젠 광고 만들어줘", "UI로 디멘드젠 광고 생성", "승인 기다리지 말고 지금"처럼 즉시 광고를
  원하면, 서버 없이 Google Ads UI를 내장 브라우저로 띄워 캠페인을 초안까지 생성하는 A 방식도
  지원한다(references/ui-copilot-demandgen-campaign.md).
  기존 google-ads-direct-mcp 스킬의 서버 자산을 재사용하는 얇은 자동화 레이어다. Windows·macOS·Linux 지원.
---

# Google Ads 디멘드젠 셋업 자동화

이 스킬은 **디멘드젠 광고를 설정할 수 있는 환경 자체**를, 손으로 클릭하는 대신 **스크립트로 자동화**해
어느 PC에서든 동일하게 재현되게 만든다. 서버 로직(디멘드젠 툴 8개 포함)은 자매 스킬
`google-ads-direct-mcp` 의 `assets/` 를 **재사용**하고, 이 스킬은 그 위에서 돌아가는 오케스트레이션·검증·
에러 진단 레이어다. 새로 서버를 만들지 않는다.

```
[첫 PC] bootstrap.py  →  preflight → 의존성 → OAuth → 배포(Cloud Run) → 등록 → 검증
[새 PC] connect_new_pc.py → URL·토큰만으로 등록 → 상태 확인  (서버 재배포 없음)
```

전제(사용자가 이미 정한 값): 인증은 **일반 Gmail → OAuth 방식**, 재현은 **Cloud Run(클라우드 서버) 모델**.

## 언제 이 스킬을 쓰나
- "디멘드젠 광고 설정 가능하게 셋팅해줘", "환경 구성해줘"
- "다른 PC에서도 똑같이 되게 자동화 파일/스크립트로 만들어줘"
- "새 PC를 서버에 연결", "디멘드젠 준비됐는지 검증"

이미 **캠페인을 실제로 만드는** 대화(예산/타게팅/소재 입력 → 생성)는 서버가 배포된 뒤
`google-ads-direct-mcp` 의 툴로 진행한다. 이 스킬은 그 **직전까지의 환경 구축**을 담당한다.

## 파일 구성
| 경로 | 역할 |
|------|------|
| `scripts/guided_setup.py` | ★from-zero 마법사: MCC·개발자토큰(안내+검증) / GCP프로젝트·Ads API(자동) / OAuth클라이언트(반자동) |
| `scripts/preflight.py` | 환경 진단 — Python·gcloud·로그인·결제·설정 파일 상태를 OK/누락으로 리포트 |
| `scripts/install_gcloud.py` | gcloud CLI 자동 설치(OS별: winget/brew/공식 스크립트) + 로그인·프로젝트 설정 |
| `scripts/bootstrap.py` | ★첫 PC 원커맨드: gcloud설치→진단→의존성→OAuth→배포→등록→검증. 실패 시 자동 진단 |
| `scripts/connect_new_pc.py` | ★새 PC: URL+베어러 토큰만으로 `claude mcp add` + 상태 확인 |
| `scripts/verify_demandgen.py` | 디멘드젠 설정 가능 검증(인증·읽기 + validate_only 쓰기 확인, 과금 없음) |
| `scripts/link_account.py` | 광고계정을 MCC 아래로 연결(초대 API + Test 등급이면 UI 수락 안내 + 상태 확인) |
| `scripts/_common.py` | 공통 헬퍼 + **에러 자동 진단**(알려진 오류 → 원인·해결) |
| `config/.env.example` | 개발자 토큰·프로젝트·MCC/고객 ID 템플릿 |
| `references/troubleshooting.md` | 막히는 지점별 상세 해결 |
| `references/manual-fallback.md` | 스크립트 실패 시 손으로 하는 순서 |
| `references/browser-copilot-walkthrough.md` | ★내장 브라우저 코파일럿 실행 명세 — 수동 웹 단계를 Claude가 옆에 브라우저 띄워 안내·검증 |
| `references/ui-copilot-demandgen-campaign.md` | ★A 방식: 서버 없이 Google Ads UI로 디멘드젠 캠페인을 클릭 단위로 "생성"(초안). API 승인 대기 불필요 |

설정·비밀값은 모두 config 디렉터리(기본 `~/.google-ads-mcp`, 환경변수 `GOOGLE_ADS_MCP_HOME` 로 변경)에 둔다.

---

## 실행 절차 (에이전트 가이드)

> **★ 브라우저 코파일럿 모드**: 사용자가 "옆에 브라우저 켜서 같이 진행 / 가이드해줘 / 직접 못 하는 걸
> 안내해줘" 라고 하면(또는 완전 초보라 스크립트 대신 함께 클릭하길 원하면), 수동 웹 단계(MCC·토큰·GCP
> 결제·OAuth 동의화면·클라이언트·리프레시 토큰)를 **Claude Code 내장 브라우저**(`mcp__Claude_Browser__*`)로
> 열어 **클릭 단위로 안내·검증**하며 진행한다. 실행 명세는 `references/browser-copilot-walkthrough.md`.
> 원칙: 에이전트는 열고·읽고·짚어주고·비민감 필드를 채우고·결과를 검증한다. **로그인·reCAPTCHA·카드·
> 동의·제출·파일 업로드는 사용자**가 한다. 내장 브라우저가 막히는 지점(광고계정 본인인증, OAuth 클라이언트
> "만들기" 버튼)은 시스템 크롬으로 전환 안내. 자동화 스크립트 경로와 병행·교차 사용 가능.

> **★ 두 가지 광고 생성 방식 (혼동 주의)**:
> - **A 방식 — UI 코파일럿 생성 (서버 불필요, 오늘 바로 됨)**: 사용자가 "지금 디멘드젠 광고 만들어줘 / UI로
>   안내해줘 / 승인 기다리지 말고" 라고 하면 `references/ui-copilot-demandgen-campaign.md` 대로 내장
>   브라우저로 Google Ads UI를 띄워 캠페인을 **초안까지 클릭 단위로 생성**. 개발자 토큰·배포·Basic 승인
>   전부 불필요. 실제 게재만 광고계정 결제 + 사용자 확인 필요.
> - **B 방식 — API/MCP 자동 생성 (이 스킬로 서버 구축)**: 아래 A~D 절차로 서버를 배포하면 대화만으로
>   `create_demand_gen_campaign` 등 API로 생성·재현. 개발자 토큰 Basic 승인 필요.
> "지금 당장"이면 A, "자동화·재현·다PC"면 B.

### A. 첫 PC — 최초 1회 구축

0. **처음부터(계정도 없음)라면 가이드 마법사**. 자동 가능한 건 자동, 웹 전용은 안내+검증:
   ```bash
   python scripts/guided_setup.py
   ```
   - **1) MCC 생성 / 2) 개발자 토큰**: 웹 가입·reCAPTCHA·Google 심사라 **자동화 불가** → 해당 페이지를
     열어주고 단계별로 안내한 뒤, 붙여넣은 **MCC ID(10자리)·토큰(22자)을 검증**해 `.env` 에 저장.
   - **3) GCP 프로젝트 + 결제 + Ads API 활성화**: gcloud 로 **완전 자동**(기존 프로젝트 재사용도 가능).
   - **4) OAuth 클라이언트 JSON**: 콘솔에서 생성은 수동(안내), 이후 config 로 복사 자동.
   이미 값이 있으면 각 단계는 자동으로 건너뛴다. 끝나면 아래 1번 이후로 이어진다.
   웹 전용 단계(MCC 생성·토큰·OAuth 클라이언트)는 **완전 초보자 기준 클릭 단위 안내**를 화면에 출력하고,
   같은 내용을 `references/beginner-setup-guide.md` 에도 문서로 둔다.

1. **진단부터**. 무엇이 준비됐고 무엇이 없는지 먼저 보여준다.
   ```bash
   python scripts/preflight.py
   ```
   `fail` 항목이 있으면 각 항목의 해결책을 사용자에게 안내하고, 사용자가 처리하도록 돕는다.
   특히 자주 막히는 것: **개발자 토큰 Test 등급(→ Basic 신청)**, **GCP 결제 미연결**,
   **OAuth 클라이언트 JSON 미저장**. (상세: `references/troubleshooting.md`)

2. **필요한 값 수집**. `.env` 가 없으면 bootstrap 이 템플릿을 복사해 준다. 사용자에게
   개발자 토큰, `GOOGLE_PROJECT_ID`, MCC ID(`GOOGLE_ADS_LOGIN_CUSTOMER_ID`),
   광고계정 ID(`GOOGLE_ADS_CUSTOMER_ID`) 를 물어 `~/.google-ads-mcp/.env` 에 채운다.
   OAuth 클라이언트 JSON(Desktop app)은 `~/.google-ads-mcp/google_ads_oauth_client.json` 에 둔다.

3. **원커맨드 실행**.
   ```bash
   python scripts/bootstrap.py          # 프롬프트 확인하며 진행
   python scripts/bootstrap.py --yes    # gcloud 설치 등 확인을 자동 승인
   ```
   내부적으로 9단계를 진행하며, 각 단계 실패 시 **원인·해결을 자동 출력**한다.
   - **gcloud 가 없으면 자동 설치**(OS별)한 뒤 같은 세션에서 이어서 배포까지 진행(PATH 자동 주입).
   - OAuth 단계는 브라우저(또는 출력 URL) 동의가 1회 필요 → 대화형이므로 사용자에게 안내.
   - 리프레시 토큰을 새로 받고 싶으면 `--reauth`, 리전 변경은 `--region us-central1`.
   - gcloud 만 따로 설치하려면: `python scripts/install_gcloud.py`.
   완료되면 **MCP URL** 과 **베어러 토큰**(=광고비 권한)이 `~/.google-ads-mcp/connection.json` 에 저장된다.

4. **검증**은 bootstrap 마지막 단계에서 자동 실행되지만, 따로도 가능:
   ```bash
   python scripts/verify_demandgen.py
   ```
   인증·읽기(self-test) + 디멘드젠 툴 존재 + **validate_only 쓰기 확인(실제 생성/과금 없음)** 을 거쳐
   "디멘드젠 광고 설정 준비 완료 ✅" 를 확인한다.

### B. 다른 PC — 이후 반복 (서버 재배포 없음)

첫 PC에서 나온 **MCP URL + 베어러 토큰**만 있으면 된다. `connection.json` 을
`claude-config-cloud-sync`(구글드라이브 등)로 동기화해 두면 인자 없이도 동작한다.
```bash
python scripts/connect_new_pc.py                          # connection.json 자동 사용
python scripts/connect_new_pc.py --url <…/mcp> --token <베어러>   # 직접 전달
```
`claude mcp add` 자동 실행 + `/healthz` 상태 확인까지 한다. 같은 서버를 쓰므로 디멘드젠 설정도 동일하게 가능.

### C. 관리자(MCC) 계정에 광고계정 연결 (선택 — 중앙 관리/데이터 조회)

광고계정을 MCC 아래로 정리하고 싶을 때. **관리자 계정 자체는 API 로 만들 수 없다**(웹 가입 전용);
이미 있는 MCC 에 **일반 광고계정을 연결(invite→accept)** 하는 것만 자동화한다.
```bash
python scripts/link_account.py --manager <MCC_ID> --client <광고계정_ID>
python scripts/link_account.py --check --manager <MCC_ID> --client <광고계정_ID>   # 상태만 확인
```
- 스크립트가 MCC→광고계정 **초대(PENDING)** 를 API 로 보낸다.
- 이어서 API 자동 수락을 시도하되, **개발자 토큰이 Test/Explorer 등급이면 수락이 막힌다**
  (`DEVELOPER_TOKEN_NOT_APPROVED` / "explorer access"). 이때 스크립트가 **Google Ads UI 에서
  직접 수락하는 단계**(광고계정 → 설정 → 액세스 및 보안 → 관리자 탭 → 수락)를 출력한다. UI 수락은
  토큰 등급과 무관하게 동작한다.
- 수락 후 `--check` 로 ACTIVE 확인 → `--set-login` 으로 `.env` 의 `LOGIN_CUSTOMER_ID` 를 MCC 로 바꾸고
  `bootstrap.py --yes` 재배포하면 **MCC 경유 데이터 조회/관리**가 된다.
- 참고: 단순 **데이터 조회(읽기)는 Test 등급 + 직접 접근으로도 이미 가능**하다. MCC 연결과 Basic 승인은
  실제 쓰기(캠페인 생성)·중앙 관리·API 자동화 확장을 위한 것.

### D. 실사용 (셋업 이후)
Claude Code에서 **읽기부터**: "내 구글애즈 계정 목록 보여줘" → 최근 성과 조회.
그 다음 디멘드젠 캠페인은 **PAUSED 로 생성**해 UI 확인 후에만 활성화(`confirm_enable='ENABLE'`).
디멘드젠 조립 흐름과 API 제약(입찰은 전환/클릭 기반, 영상은 YouTube ID, 이미지는 URL)은
`google-ads-direct-mcp` 스킬 참조.

---

## 막히는 지점 요약 (자동 진단이 잡는 것들)
`scripts/_common.py` 의 `diagnose()` 가 다음을 감지해 원인·해결을 함께 출력한다:
개발자 토큰 미승인, 리프레시 토큰 만료, 계정 권한 없음, 고객 ID 오류, 결제 미연결,
시크릿 접근 권한, API 미활성화, gcloud 로그인/권한, 의존성 누락, 쿼터 초과.
매칭 안 되는 오류는 `references/troubleshooting.md` 로 안내한다.

## 안전 원칙
- 비밀값(`.env`, `google_ads_adc.json`, `mcp_bearer_token.txt`, OAuth 클라이언트 JSON,
  `connection.json`)은 **절대 커밋/공유 금지**. 베어러 토큰을 가진 사람은 연결된 광고계정을 조작할 수 있다.
- 검증은 `validate_only`(과금·생성 없음)를 기본으로 한다. 실제 캠페인은 PAUSED 로만 만들고,
  활성화·삭제는 명시적 확인 인자를 요구한다.
