# 수동 폴백 — 스크립트가 막힐 때 손으로 하는 순서

`bootstrap.py` 가 어떤 단계에서 실패하면, 그 단계만 아래 수동 절차로 처리한 뒤 다시
`python bootstrap.py` 를 돌리면 된다(이미 끝난 단계는 자동으로 건너뛴다).
전체 개념·화면 경로는 상위 `google-ads-direct-mcp` 스킬의 SKILL.md 와 동일하다.

## 사전 준비 (0단계)
1. **관리자(MCC) 계정**: https://ads.google.com/home/tools/manager-accounts → 관리자 계정 만들기 →
   실제 광고계정을 이 MCC 아래에 연결. MCC ID(하이픈 없이 10자리) 기록.
2. **GCP 프로젝트 + 결제**: https://console.cloud.google.com → 프로젝트 생성 → 결제 계정 연결.
3. **gcloud CLI**: https://cloud.google.com/sdk/docs/install → `gcloud init` → `gcloud auth login`.
4. **Python 3.10+**: https://www.python.org/downloads/ (Windows 는 'Add to PATH' 체크).

## 1. 개발자 토큰 (Step 1)
관리자 계정 → 도구 및 설정 → **API 센터** → 개발자 토큰 확인/신청.
- 일반 Gmail 은 처음 **Test 등급** → 실계정엔 **Basic 승인** 신청 필요(1~3영업일).
- 22자 토큰을 `~/.google-ads-mcp/.env` 의 `GOOGLE_ADS_DEVELOPER_TOKEN` 에 기록.

## 2. OAuth 클라이언트 JSON (Step 2)
Cloud Console → **APIs & Services**:
1. **Google Ads API** enable.
2. **OAuth consent screen** 구성 (External, 본인 계정을 test user 로 추가).
3. **Credentials → Create Credentials → OAuth client ID → Application type: Desktop app** → JSON 다운로드.
4. 받은 파일을 `~/.google-ads-mcp/google_ads_oauth_client.json` 로 저장.

## 3. .env 채우기 (Step 3)
`config/.env.example` 를 `~/.google-ads-mcp/.env` 로 복사하고 값 입력:
개발자 토큰, `GOOGLE_PROJECT_ID`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`(MCC), `GOOGLE_ADS_CUSTOMER_ID`(광고계정).

## 4. 리프레시 토큰 (Step 4) — bootstrap 의 OAuth 단계 수동 실행
```
cd <skills>/google-ads-direct-mcp/assets
pip install -r requirements.txt
python oauth_setup.py     # 브라우저(또는 URL)에서 동의 → google_ads_adc.json 생성
```

## 5. 로컬 검증 (Step 5)
```
python google_ads_mcp_server.py --self-test
```
계정 목록이 나오면 인증 OK. 안 나오면 troubleshooting.md 참고.

## 6. 베어러 토큰 (Step 6)
```
python -c "import secrets; print(secrets.token_urlsafe(32))" > ~/.google-ads-mcp/mcp_bearer_token.txt
```

## 7. 배포 (Step 7)
```
cd <skills>/google-ads-direct-mcp/assets
MCP_BEARER_TOKEN=$(cat ~/.google-ads-mcp/mcp_bearer_token.txt) python deploy_cloud_run.py
# Windows PowerShell:
#   $env:MCP_BEARER_TOKEN = Get-Content ~/.google-ads-mcp/mcp_bearer_token.txt
#   python deploy_cloud_run.py
```
끝나면 `MCP_URL` 출력.

## 8. Claude Code 등록 (Step 8)
```
claude mcp add --transport http --scope user google_ads_direct <MCP_URL> \
  --header "Authorization: Bearer <베어러 토큰>"
claude mcp list
```

## 9. 다른 PC
같은 `<MCP_URL>` 과 베어러 토큰으로 위 8번만 반복하면 된다(서버 재배포 불필요).
스크립트로는 `python connect_new_pc.py --url <MCP_URL> --token <토큰>`.
