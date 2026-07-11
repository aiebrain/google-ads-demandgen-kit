# 문제 해결 — 디멘드젠 셋업에서 막히는 지점

스크립트가 실패하면 대부분 `_common.py`의 자동 진단이 원인·해결을 함께 출력한다.
여기서는 그 진단이 가리키는 항목을 더 자세히 설명한다. (자동 진단 키워드는 각 항목 끝의 `signature`)

## 1. 개발자 토큰이 Test 등급 — `DEVELOPER_TOKEN_NOT_APPROVED`
- **증상**: 읽기는 되는데 실계정 호출에서 막힘, 또는 self-test 에서 이 에러.
- **원인**: 일반 Gmail 로 발급한 토큰은 처음엔 Test 등급이라 **테스트 계정만** 호출 가능.
- **해결**: 관리자(MCC) → 도구 및 설정 → **API 센터**(https://ads.google.com/aw/apicenter) 에서
  **Basic 액세스 신청**. 승인까지 보통 1~3영업일. 승인 전에는 디멘드젠을 실계정에 설정 불가.
- `signature: DEVELOPER_TOKEN_NOT_APPROVED`

## 2. OAuth 리프레시 토큰 만료 — `invalid_grant`
- **증상**: 한동안 잘 되다가 갑자기 인증 실패.
- **원인**: 리프레시 토큰이 취소/만료(비밀번호 변경, 앱 권한 회수, 미사용 등).
- **해결**: `python bootstrap.py --reauth` → 새 `google_ads_adc.json` 생성 → 자동 재배포.
- `signature: invalid_grant`

## 3. 인증 계정에 광고계정 권한 없음 — `USER_PERMISSION_DENIED`
- **원인**: OAuth 동의 때 쓴 구글 계정이 대상 광고계정(또는 MCC)에 접근 권한이 없음.
- **해결**: Google Ads → 관리자 → 액세스 및 보안에서 그 계정에 권한을 부여하거나,
  권한 있는 계정으로 `--reauth`. OAuth consent screen 에 본인 계정이 test user 로 있어야 함.
- `signature: USER_PERMISSION_DENIED`

## 4. 고객 ID 오류 — `CUSTOMER_NOT_FOUND` / `INVALID_CUSTOMER_ID`
- **흔한 실수**: `GOOGLE_ADS_CUSTOMER_ID` 에 **관리자(MCC) ID** 를 넣음. 광고가 실제로 도는
  **하위 광고계정 ID** 를 넣어야 한다. `LOGIN_CUSTOMER_ID` 는 MCC, `CUSTOMER_ID` 는 광고계정.
- 둘 다 **하이픈 없이 숫자 10자리**.
- `signature: CUSTOMER_NOT_FOUND`

## 5. 결제 미연결 — 배포 실패
- **원인**: Cloud Run/Build 는 결제 연결된 프로젝트에서만 동작.
- **해결**: https://console.cloud.google.com/billing 에서 결제 계정 연결. 무료 한도 넉넉.
- `signature: billing / FAILED_PRECONDITION`

## 6. 시크릿 접근 권한 — 컨테이너가 부팅 중 죽음
- **증상**: 배포는 됐는데 서비스가 시작 못 하고 permission denied.
- **원인**: Cloud Run 런타임 서비스계정에 `secretAccessor` 없음.
- **해결**: `deploy_cloud_run.py` 가 자동 부여하지만 실패 시 수동:
  ```
  gcloud secrets add-iam-policy-binding google-ads-adc-json \
    --member serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com \
    --role roles/secretmanager.secretAccessor
  ```
  (3개 시크릿 모두: google-ads-developer-token, google-ads-adc-json, google-ads-mcp-bearer-token)
- `signature: secretmanager permission`

## 7. API 미활성화 — `SERVICE_DISABLED`
- **해결**: `gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com`
  (bootstrap/deploy 가 자동 시도).
- `signature: SERVICE_DISABLED`

## 8. gcloud 로그인/권한
- **로그인 없음**: `gcloud auth login`. 프로젝트 지정: `gcloud config set project <ID>`.
- **역할 부족**(`The caller does not have permission`): 프로젝트에서 본인 계정에 Owner,
  또는 (Cloud Run Admin + Cloud Build Editor + Service Account User + Secret Manager Admin).
- `signature: gcloud auth login / The caller does not have permission`

## 9. 서비스 계정을 쓰려다 막힘 (일반 Gmail)
- Grok 가이드가 권한 서비스 계정(Service Account) 방식은 **Google Workspace 도메인 + 도메인 전체
  위임** 이 있어야만 Google Ads API 에서 동작한다. 일반 @gmail.com 계정은 **OAuth 방식**(이 스킬의
  기본)을 써야 한다. 서비스 계정 JSON 으로는 `USER_PERMISSION_DENIED` 가 난다.

## 11. "The developer token is not valid" (미승인이 아니라 값 오류)
- **증상**: self-test 에서 `The developer token is not valid`. `NOT_APPROVED`(Test 등급)와 **다름**.
- **원인**: 토큰 문자열이 틀렸거나(오타/일부 누락), 재발급돼 예전 값이 남았거나, 토큰을 발급한
  MCC 와 `GOOGLE_ADS_LOGIN_CUSTOMER_ID` 가 다른 계정.
- **해결**: 관리자(MCC) → 도구 및 설정 → **API 센터**(https://ads.google.com/aw/apicenter)에서
  개발자 토큰을 **다시 복사**해 `~/.google-ads-mcp/.env` 의 `GOOGLE_ADS_DEVELOPER_TOKEN` 에 정확히 붙여넣기.
  그 MCC ID 가 `GOOGLE_ADS_LOGIN_CUSTOMER_ID`(1234567890 등)와 같은지 확인. 재배포는 `bootstrap.py`.
- `signature: developer token is not valid`

## 12. "Claude Code cannot be launched inside another Claude Code session"
- **증상**: 등록 단계에서 `claude mcp add` 가 중첩 세션 오류로 실패.
- **원인**: Claude Code 세션 안에서 `claude` CLI 를 실행하면 `CLAUDECODE` 환경변수 때문에 차단됨.
- **해결**: 스크립트는 이제 자동으로 `CLAUDECODE` 를 빼고 실행한다. 수동으로는:
  `env -u CLAUDECODE claude mcp add --transport http --scope user google_ads_direct <URL> --header "Authorization: Bearer <토큰>"`
  (PowerShell: `$env:CLAUDECODE=''; claude mcp add ...`). 또는 그냥 별도(일반) 터미널에서 실행.
- `signature: cannot be launched inside another Claude Code`

## 10. 새 PC 연결이 안 됨 (connect_new_pc.py)
- **healthz 실패**: URL 오타, 서버 미배포/중지. 첫 PC 에서 배포가 성공했는지 확인.
- **claude CLI 없음**: 출력된 `claude mcp add …` 명령을 수동 실행.
- **토큰 불일치**: 첫 PC 의 `connection.json` 또는 `mcp_bearer_token.txt` 값과 동일해야 함.
