# 새 PC · 새 계정에서 처음부터 재현하기 (Step 1~9 전체)

> 오늘 `your-account` 계정으로 완주한 셋업을, **다른 PC**나 **다른 구글 계정**에서 그대로 재현하기 위한
> 마스터 런북입니다. 상세 클릭 단계는 함께 들어있는 두 가이드를 참조하고, 이 문서는 "무엇을 어떤 순서로,
> 무엇은 자동/무엇은 직접" 을 한눈에 정리합니다.

---

## 0. 먼저 — 무엇이 재현되고, 무엇은 계정마다 직접 해야 하나

**이식·자동화 가능 (이 키트로 옮겨감):**
- 서버 코드(디멘드젠 툴 포함), 배포 자동화 스크립트, 두 개의 셋업 가이드, Basic 신청용 설계문서 템플릿,
  새 PC 연결 스크립트.

**계정마다 반드시 직접 (공유·자동화 불가 — 구글 보안·심사 때문):**
- 관리자(MCC) 계정 생성 — reCAPTCHA
- 개발자 토큰 발급 + **Basic 액세스 승인(최대 5영업일)** — 구글 심사
- OAuth 동의(허용) / GCP 결제 카드 등록 / 광고계정 본인 인증

**절대 공유 금지 (계정마다 새로 생성):**
- `~/.google-ads-mcp/` 의 `.env`, `google_ads_adc.json`, `google_ads_oauth_client.json`,
  `mcp_bearer_token.txt`, `connection.json` — 전부 비밀값. 다른 계정/사람에게 넘기지 말 것.

> 요약: **코드·가이드·템플릿은 공유 OK. 비밀값과 "구글이 사람에게 요구하는 단계"는 각 계정이 직접.**

---

## 0.5. 서버 없이 "지금 당장" 광고만 만들기 (A 방식 — 실측 검증됨)

디멘드젠 **광고를 지금 바로** 만들고 싶고 자동화·재현은 나중이어도 되면, **아래 서버 구축(1~9)을 기다릴
필요 없이** Google Ads UI로 바로 만들 수 있습니다. 필요한 건 **광고를 만들 계정 로그인** 하나뿐.

- Claude Code에서: **"지금 디멘드젠 광고 만들어줘 / UI로 안내해줘"** → 스킬이
  `references/ui-copilot-demandgen-campaign.md` 대로 내장 브라우저로 Google Ads를 띄워 캠페인을
  **초안까지 클릭 단위로 생성**합니다. (실제로 이 방식으로 완주 검증됨)
- 핵심: 입찰 **클릭수**(전환추적 회피) / EU정치광고 **아니요** / 이미지는 **최종 URL의 사이트 스캔 추천
  이미지**(파일 업로드 불필요) / 예산·게시는 **사용자 확인** / 결과는 **초안**(미게시).
- 실제 게재만 그 **광고계정에 결제 카드** 연결 + 사용자 확인 필요. API 승인과 무관.

> A(지금 UI 생성) vs B(아래 서버 구축=API 자동화·재현): "당장"이면 A, "대화 한마디로 자동·다PC"면 B.

## 1. 새 계정으로 처음부터 — API 자동화 서버 구축 (Step 1~9, = B 방식)

### 준비물 (새 계정/새 PC)
- 새 구글 계정(Gmail) 1개 — **처음부터 끝까지 이 계정 하나로만**
- 카드 1장(GCP 결제용), Python 3.10+, gcloud CLI, Claude Code
- 이 키트의 두 스킬을 `~/.claude/skills/` 에 복사

### 두 가지 실행 경로 (택1, 섞어도 됨)

**(A) 자동화 스킬 경로 — 정상 브라우저 환경에서 권장**
```bash
python scripts/guided_setup.py     # MCC·토큰 안내+검증 / GCP·API 자동 / OAuth 클라이언트 반자동
python scripts/preflight.py        # 준비 상태 진단
python scripts/bootstrap.py --yes  # 의존성→OAuth→배포→등록→검증 (원커맨드)
python scripts/verify_demandgen.py # "디멘드젠 준비 완료 ✅"
```

**(B) 수동 가이드 경로 — 확실함 (오늘 실제로 이 경로로 완주)**
- 웹 단계 클릭: [디멘드젠-셋업-가이드.md](디멘드젠-셋업-가이드.md) (Part 1) 또는
  Claude가 브라우저를 띄워 함께 진행: [앱내장브라우저-진행가이드.md](앱내장브라우저-진행가이드.md)
- 배포/자격증명은 아래 단계 매핑대로.

### 단계 매핑 (새 계정 기준)

| Step | 내용 | 자동/직접 | 참고 |
|------|------|-----------|------|
| 1 | 관리자(MCC) 계정 생성 (서울/KRW) | 직접(reCAPTCHA) | 국가·시간대·통화 변경 불가 — 반드시 서울/KRW |
| 2 | 개발자 토큰 발급(Test) + **Basic 신청서 제출** | 직접(심사 5일) | 신청서에 **GCP 프로젝트ID·설계문서 필수** → Step 3 먼저 |
| 3 | GCP 프로젝트 + 결제 + Ads API 활성화 | 프로젝트·API 자동 / 카드 직접 | `gcloud projects create`, 결제는 **활성화 최대 24h** |
| 4 | OAuth 동의화면(외부·테스트) + 테스트사용자 + 데스크톱 클라이언트 | 반자동 / JSON 다운로드 직접 | 새 "Google 인증 플랫폼" 4단계 마법사 |
| 5 | OAuth 동의 → 리프레시 토큰 | `python -u oauth_setup.py` + 브라우저 "허용" | adc.json 생성 |
| 6 | 로컬 self-test | `python google_ads_mcp_server.py --self-test` | 계정 목록 나오면 OK |
| 7 | Cloud Run 배포 | `python deploy_cloud_run.py`(또는 bootstrap) | **GCP 결제 활성화 후에만** |
| 8 | Claude Code 등록 | `claude mcp add ...` | URL+베어러 토큰 |
| 9 | 디멘드젠 검증 | `python verify_demandgen.py` | 준비 완료 |

### Basic 신청서: 설계문서 재사용
[basic-access-design-doc.md](basic-access-design-doc.md)(및 .pdf) 템플릿에서 **회사명·웹사이트·GCP
프로젝트ID·MCC ID**만 새 계정 값으로 바꿔 제출하면 됩니다. 나머지 구조(자가사용 internal, API 사용,
보안 설계)는 동일하게 재사용 가능. (도구를 고객/타인 계정 관리용으로 쓸 거면 "External"로 수정)

---

## 2. 새 PC에서 기존 서버 연결 (같은 계정 — 재배포 없음)

서버가 이미 배포돼 있으면, 새 PC는 **MCP URL + 베어러 토큰**만 있으면 됩니다. OAuth·배포 다시 안 함.

```bash
python scripts/connect_new_pc.py --url https://<서비스>.run.app/mcp --token <베어러토큰>
# 또는 수동
claude mcp add --transport http google_ads_direct https://<서비스>.run.app/mcp \
  --header "Authorization: Bearer <베어러토큰>"
claude mcp list
```

- 값은 첫 PC의 `~/.google-ads-mcp/connection.json` 에 있음. 이 파일을 구글드라이브 등으로 동기화하면
  (`claude-config-cloud-sync` 스킬) 인자 없이도 연결됨.
- ⚠️ **베어러 토큰 = 광고비 권한.** 안전 채널로만 전달. 이 토큰 가진 사람은 연결 광고계정을 조작 가능.
- ⚠️ **내구성**: OAuth 앱이 "테스트 중"이면 리프레시 토큰이 **7일마다 만료**. 여러 PC에서 오래 쓰려면
  OAuth 앱을 **"게시(Production)"** 로 전환(만료 없어짐, "미확인 앱" 경고는 클릭으로 통과).

---

## 3. 이 키트에 들어있는 것 (복사 위치)

| 자산 | 새 PC에서의 위치 |
|------|------------------|
| `google-ads-direct-mcp/` (서버 본체 + 배포 스크립트) | `~/.claude/skills/google-ads-direct-mcp/` |
| `google-ads-demandgen-setup/` (자동화 오케스트레이션) | `~/.claude/skills/google-ads-demandgen-setup/` |
| `디멘드젠-셋업-가이드.md` (전체 셋업 가이드) | 아무 곳 (참고용) |
| `앱내장브라우저-진행가이드.md` (Claude 코파일럿 진행) | 아무 곳 (참고용) |
| `basic-access-design-doc.md/.pdf` (Basic 신청 설계문서 템플릿) | 아무 곳 (값만 바꿔 제출) |
| `재현-가이드-새PC-새계정.md` (이 문서) | 아무 곳 (마스터 런북) |

설정·비밀값은 각 PC의 `~/.google-ads-mcp/` 에 **각자 생성**(복사해오지 말 것).

---

## 4. 빠른 체크리스트

**새 계정 처음부터:**
- [ ] 스킬 2개 `~/.claude/skills/` 복사, gcloud 로그인(그 계정)
- [ ] MCC 생성(서울/KRW) → MCC ID
- [ ] GCP 프로젝트 생성 + 결제 카드 + Ads API
- [ ] 개발자 토큰 발급 → Basic 신청서(프로젝트ID+MCC+설계문서) 제출
- [ ] OAuth 동의화면(외부·테스트) + 테스트사용자 + 데스크톱 클라이언트 JSON
- [ ] `oauth_setup.py`(-u) → adc.json → `--self-test` 통과
- [ ] (결제 활성화 후) 배포 → `claude mcp add` → `verify_demandgen.py`

**같은 서버 새 PC:**
- [ ] MCP URL + 베어러 토큰 확보(안전 채널)
- [ ] `connect_new_pc.py` 또는 `claude mcp add`
- [ ] `claude mcp list` 로 확인
