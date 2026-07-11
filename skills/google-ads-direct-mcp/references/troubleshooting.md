# 문제 해결 · 함정 모음

실제 배포에서 자주 걸리는 것들. 증상 → 원인 → 해결 순.

## 인증 에러 (Step 5 self-test / Step 8 첫 호출)

### `DEVELOPER_TOKEN_NOT_APPROVED`
개발자 토큰이 아직 Test 등급이라 운영 계정을 못 부른다. → API Center에서 **Basic 이상 승인** 신청.
승인 전엔 테스트용 고객 계정으로만 검증 가능.

### `authentication error` / `invalid_grant`
리프레시 토큰이 만료·취소됐거나 client_id/secret 불일치. → `oauth_setup.py`를 다시 실행해
`google_ads_adc.json`을 새로 만든다. OAuth consent screen이 "Testing" 상태면 리프레시 토큰이
7일 만에 만료될 수 있으니, 본인을 test user로 넣거나 앱을 게시(Production)한다.

### `USER_PERMISSION_DENIED` 또는 특정 계정만 실패
로그인 고객 ID(MCC)와 대상 고객 ID 관계 문제. → `GOOGLE_ADS_LOGIN_CUSTOMER_ID`에 MCC ID(숫자만)
설정. 대상 계정이 그 MCC 밑에 연결돼 있는지 확인. `customer_children`로 보이는 계정만 조작 가능.

## 배포 에러 (Step 7)

### `PERMISSION_DENIED: ... secretmanager` / 컨테이너가 시작하자마자 죽음
Cloud Run 런타임 서비스계정에 시크릿 접근 권한이 없다. → 각 시크릿에 접근 롤 부여:

```bash
PROJ=$(gcloud config get-value project)
NUM=$(gcloud projects describe $PROJ --format='value(projectNumber)')
SA="$NUM-compute@developer.gserviceaccount.com"
for S in google-ads-developer-token google-ads-adc-json google-ads-mcp-bearer-token; do
  gcloud secrets add-iam-policy-binding $S \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
done
```
그 후 `python deploy_cloud_run.py` 재실행.

### `API [run.googleapis.com] not enabled` 등
`deploy_cloud_run.py`가 활성화하지만, 수동 배포라면 먼저:
```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com
```

### 컨테이너가 `PORT`에서 안 뜬다
Cloud Run은 `PORT`(기본 8080)를 주입한다. Dockerfile의 CMD가 `${PORT}`를 쓰는지 확인. 하드코딩 금지.

### Windows에서 시크릿 생성 명령이 안 먹힌다
`deploy_cloud_run.py`는 파이썬이라 OS 무관하게 동작한다. 수동으로 `printf ... | gcloud`를 쓸 경우
그건 bash 문법 — PowerShell에선 `"값" | gcloud secrets create NAME --data-file=-`로.

## 연결 에러 (Step 8)

### `claude mcp list`에 뜨는데 툴 호출이 401
베어러 토큰 불일치. 등록할 때 헤더 값과 Secret Manager의 `google-ads-mcp-bearer-token`이 같은지 확인.
토큰을 바꿨으면 시크릿 새 버전 추가 후 재배포하고, Claude Code에도 재등록.

### `/healthz`는 되는데 `/mcp`가 이상
`/healthz`·`/`는 인증 없이 열려 있고 `/mcp`만 베어러를 요구한다(정상). curl로 `/mcp`를 그냥 치면
세션 협상이 안 돼 이상해 보일 수 있으니, 최종 확인은 반드시 Claude Code MCP 툴로 한다.

## 보안 체크리스트

- [ ] `.env`, `google_ads_adc.json`, `mcp_bearer_token.txt`, OAuth 클라이언트 JSON은 **커밋·업로드 금지** (`.gcloudignore`로 이미지에서도 제외됨)
- [ ] 베어러 토큰은 `secrets.token_urlsafe(32)` 급의 무작위값
- [ ] 비밀은 Secret Manager에만. Dockerfile·소스·Git·Drive에 두지 않음
- [ ] 공개 URL + 베어러 토큰 조합은 "토큰 = 광고비 권한"임을 인지. 더 강한 격리가 필요하면 IAM 인증 또는 IP 제한(Cloud Armor) 검토
- [ ] enable/delete는 확인 인자 필수, 새 캠페인은 기본 PAUSED
- [ ] 로그·응답에 토큰/PII 남기지 않음 (서버가 응답을 마스킹하지만, 커스텀 툴 추가 시에도 유지)

## 캠페인 생성 시 "The required field was not present"

최신 API(v17+)는 캠페인 생성 시 **EU 정치광고 표시**가 필수다. 안 넣으면 위 에러가 뜬다.
이 서버의 검색·디멘드젠 캠페인 툴엔 이미 다음이 들어가 있다(커스텀 캠페인 툴을 새로 만들면 똑같이 넣을 것):
```python
campaign.contains_eu_political_advertising = \
    client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
```

## 디멘드젠 광고 "Invalid call to action text"

`call_to_action_text`는 **표시 문구**를 받는다: `"Learn more"`, `"Shop now"`, `"Sign up"`,
`"Subscribe"`, `"Book now"`, `"Download"` 등. `"LEARN_MORE"` 같은 enum 토큰은 거부된다. 생략도 가능.

## 커스텀 툴 추가할 때

`google_ads_mcp_server.py`의 기존 쓰기 툴을 복사해서 시작. 반드시:
- 파괴적/과금 동작은 `confirm_*` 인자 뒤에 두기
- 새로 만드는 객체는 기본 PAUSED/draft
- 반환은 `_json(...)`으로 감싸 자동 마스킹 유지
- 고객 ID는 `(customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")`로 정규화
