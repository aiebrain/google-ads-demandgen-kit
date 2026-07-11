# Google Ads 디멘드젠 광고 키트 (Claude Code)

Claude Code로 **Google Ads 디멘드젠(Demand Gen) 광고를 만들 수 있게** 해주는 스킬·가이드 모음입니다.
두 가지 방식을 모두 지원합니다:

- **A 방식 — UI 코파일럿 (서버 불필요, 지금 바로)**: Claude가 내장 브라우저로 Google Ads UI를 띄워
  디멘드젠 캠페인을 **초안까지 클릭 단위로 생성**합니다. 개발자 토큰·배포·승인 대기 없이 오늘 바로 가능.
- **B 방식 — API/MCP 자동화 (서버 구축)**: 쓰기 가능한 MCP 서버를 Cloud Run에 배포해, Claude와
  **대화만으로** 캠페인을 생성·재현합니다. 여러 PC 공유·자동화에 적합(개발자 토큰 Basic 승인 필요).

## 구성

| 경로 | 설명 |
|------|------|
| `skills/google-ads-direct-mcp/` | 쓰기 가능한 Google Ads MCP 서버 + 배포 스크립트 |
| `skills/google-ads-demandgen-setup/` | 셋업 자동화 + **코파일럿 실행 명세**(A·B 방식) |
| `디멘드젠-셋업-가이드.md` | 전체 셋업 가이드(Step 1~9, 초보자용) |
| `앱내장브라우저-진행가이드.md` | 내장 브라우저로 함께 진행하는 상세 절차 |
| `재현-가이드-새PC-새계정.md` | 다른 PC·다른 계정에서 재현하는 마스터 런북 |
| `basic-access-design-doc.md` | 개발자 토큰 Basic 액세스 신청용 설계문서 **템플릿** |
| `google-ads-direct-mcp.skill` | 단일 파일로 배포 가능한 스킬 패키지 |
| `google-ads-demandgen-reproduction-kit.zip` | 스킬+가이드 포터블 번들(다른 PC용) |

## 빠른 시작 (원클릭 흐름 — 붙여넣기)

새 Claude Code 세션에서 아래를 그대로 붙여넣으세요:

```
https://github.com/aiebrain/google-ads-demandgen-kit
이 저장소로 START-HERE.md 대로 진행해줘
```

그러면 Claude가 **권장 순서**로 진행합니다:
1. 스킬 설치(`skills/` → `~/.claude/skills/`)
2. **B 착수** — 서버 셋업으로 승인 시계 걸기(개발자 토큰 Basic 신청 ≈5영업일, GCP 결제 ≈24h)
3. **A 즉시** — 대기 동안 UI로 디멘드젠 광고를 **테스트/데모(초안)** 로 지금 바로 생성
4. **~5일 뒤** — Basic 승인되면 **B 완성**(배포·등록·검증) → 대화로 자동 생성

자세한 온보딩 절차·설명은 **[START-HERE.md](START-HERE.md)** 참고.

### 개별 트리거 (원할 때 각각)
- **"지금 디멘드젠 광고 만들어줘"** → A 방식(UI로 즉시 생성, 테스트)
- **"디멘드젠 셋업해줘"** → B 방식(API 서버 구축, 코파일럿 안내)

## 보안 주의

- 비밀값(`.env`, `google_ads_adc.json`, `google_ads_oauth_client.json`, `mcp_bearer_token.txt`,
  `connection.json`)은 **절대 커밋/공유 금지**. 이 저장소엔 포함돼 있지 않으며 `.gitignore`로 차단됩니다.
- `basic-access-design-doc.md` 는 예시 값(회사명·이메일·계정 ID)이 들어 있는 **템플릿**입니다. 다른 계정에
  쓸 때는 본인 값으로 교체하세요.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
