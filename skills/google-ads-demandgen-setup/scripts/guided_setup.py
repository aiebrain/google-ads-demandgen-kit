#!/usr/bin/env python
"""From-zero setup wizard: MCC, developer token, GCP project + Ads API, OAuth client.

Design principle — automate what CAN be automated, guide what can't:

  1. MCC (manager) account   GUIDED  — web signup with reCAPTCHA; no API to create one.
                                       Opens the page, walks you through, validates the ID.
  2. Developer token         GUIDED  — web form + Google review (1-3 days); no auto-submit.
                                       Opens API Center, then validates the 22-char token.
  3. GCP project + Ads API   AUTO    — gcloud creates the project, links billing, enables
                                       googleads.googleapis.com. Or reuses your current project.
  4. OAuth client JSON       SEMI    — created in Cloud Console (no clean CLI); opens the page,
                                       then you point the wizard at the downloaded JSON.

After this, run `python bootstrap.py` to finish (OAuth consent -> deploy -> register -> verify).
Each step is idempotent: anything already done is detected and skipped.

Usage:
    python guided_setup.py
    python guided_setup.py --only 3       # run just one phase (1|2|3|4)
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import _common as c

SIGNUP_URL = "https://accounts.google.com/signup"
MCC_URL = "https://ads.google.com/home/tools/manager-accounts"
API_CENTER_URL = "https://ads.google.com/aw/apicenter"
GCP_CONSOLE_URL = "https://console.cloud.google.com"
CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
BILLING_URL = "https://console.cloud.google.com/billing"


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"       {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return val or default


def confirm(prompt: str) -> bool:
    try:
        return input(f"       {prompt} [y/N]: ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


# --- Phase 0: Google account (the very first prerequisite) -------------------
def phase_account() -> None:
    c.step("0) 구글 계정(이메일)  —  [준비] 이후 모든 단계를 '같은' 계정 하나로 진행")
    existing = c.env_value("GOOGLE_ACCOUNT_EMAIL")
    if existing:
        c.ok(f"이번 셋업에 쓸 계정으로 기록됨: {existing}")
        return
    print(f"""       가장 먼저 '구글 계정(Gmail)'이 하나 있어야 합니다. 그리고 아주 중요한 규칙:
       ★ 1~4번(MCC·토큰·GCP·OAuth)과 결제까지 전부 '같은 구글 계정' 하나로 하세요.
         계정을 섞으면 나중에 권한 오류(로그인 차단·PERMISSION_DENIED)가 납니다.

       - 이미 Gmail 이 있으면 → 그 계정 하나로 아래 단계들을 진행하면 됩니다.
       - 없으면 → {SIGNUP_URL} 에서 새로 만드세요.
         (광고 전용으로 새 계정을 하나 파도 좋습니다. 단, 만든 뒤엔 계속 그 계정만 사용.)
       - 일반 @gmail.com 이면 됩니다. (회사 도메인 Workspace 계정은 필요 없어요.)""")
    if confirm("구글 계정을 새로 만들어야 하나요? (가입 페이지 열기)"):
        c.open_url(SIGNUP_URL)
    email = ask("이번 셋업에 사용할 구글 이메일 (기록용, 비우면 건너뜀)")
    if email:
        c.set_env_value("GOOGLE_ACCOUNT_EMAIL", email)
        c.ok(f"기록됨: {email} — 앞으로 모든 로그인은 이 계정으로 하세요.")
    else:
        c.info("건너뜀. 이후 단계는 반드시 같은 계정 하나로 로그인해 진행하세요.")


# --- Phase 1: manager (MCC) account -----------------------------------------
def phase_mcc() -> None:
    c.step("1) 관리자(MCC) 계정  —  [안내] 웹 가입(reCAPTCHA)이라 스크립트로 생성 불가")
    existing = c.env_value("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    if existing:
        c.ok(f"이미 기록된 MCC ID: {existing}")
        if not confirm("새로 만들거나 바꾸시겠어요?"):
            return
    if confirm("관리자 계정 만들기 페이지를 브라우저로 열어 드릴까요?"):
        c.open_url(MCC_URL)
    print(f"""       ── 아래 순서를 그대로 따라 하세요 (처음이어도 괜찮습니다) ──

       [1] 브라우저(크롬 등) 주소창에 아래 주소를 입력해 접속합니다:
             {MCC_URL}
       [2] 광고를 운영할 '구글 계정'으로 로그인합니다.
       [3] 파란색 "관리자 계정 만들기"(또는 Create a manager account) 버튼을 클릭합니다.
       [4] "계정 이름" 칸에 알아볼 이름을 적습니다.  예) 홍길동_광고관리
       [5] "이 관리자 계정을 어떻게 사용하시겠습니까?"가 나오면
             → 잘 모르겠으면 "다른 사람의 계정 관리"를 선택하세요(추천).
       [6] 국가 = 대한민국, 시간대 = (GMT+09:00) 서울, 통화 = KRW(대한민국 원) 선택.
             ※ 통화와 시간대는 나중에 못 바꿉니다. 한국이면 위 값 그대로 두세요.
       [7] "로봇이 아닙니다"에 체크합니다. (그림 맞히기가 나오면 풀어 주세요.)
       [8] "제출"(Submit) 버튼을 누릅니다.
       [9] "계정 탐색하기"(또는 Explore your account)를 클릭합니다.
       [10] 화면 오른쪽 위(또는 상단)에 '123-456-7890' 같은 10자리 숫자가 보입니다.
             이게 바로 '관리자(MCC) 계정 ID'입니다. 그 번호를 복사하세요.

       ※ 이 과정은 사람이 직접 해야 합니다(로봇 방지 때문에 자동으로 못 만듭니다).
         다 하셨으면 그 10자리 번호를 아래에 붙여넣으세요.""")
    mcc = digits(ask("생성된 MCC 계정 ID를 붙여넣으세요 (하이픈 무관, 비우면 건너뜀)"))
    if not mcc:
        c.warn("MCC ID 미입력 — 나중에 다시 실행하세요.")
        return
    if len(mcc) != 10:
        c.warn(f"입력값이 10자리가 아닙니다({len(mcc)}자리). 그래도 저장합니다: {mcc}")
    c.set_env_value("GOOGLE_ADS_LOGIN_CUSTOMER_ID", mcc)
    c.ok(f".env 에 GOOGLE_ADS_LOGIN_CUSTOMER_ID={mcc} 저장")


# --- Phase 2: developer token -----------------------------------------------
def phase_token() -> None:
    c.step("2) 개발자 토큰  —  [안내] 웹 양식 + Google 심사라 자동 제출 불가")
    cur = c.env_value("GOOGLE_ADS_DEVELOPER_TOKEN")
    if cur and not cur.startswith("your"):
        c.ok(f"이미 토큰이 기록돼 있습니다 (…{cur[-4:]}).")
        if not confirm("다시 입력하시겠어요?"):
            return
    if confirm("API 센터를 브라우저로 열어 드릴까요?"):
        c.open_url(API_CENTER_URL)
    print(f"""       ── 아래 순서대로 하세요 ──

       [1] 방금 만든 '관리자(MCC) 계정'으로 로그인된 상태여야 합니다.
             (오른쪽 위 계정이 그 관리자 계정인지 확인)
       [2] 주소창에 입력해 접속: {API_CENTER_URL}
       [3] "토큰 신청" 또는 "Apply for access" 버튼을 클릭합니다.
       [4] 양식을 채웁니다:
             - 이름/회사 : 본인 이름 또는 상호
             - 웹사이트   : 본인 사이트·블로그 주소 (없으면 대표 URL 아무거나)
             - 사용 목적  : "데이터 분석 및 광고 자동화" 정도로 작성
       [5] 약관에 동의하고 "제출"합니다.
       [6] 화면에 22자리 개발자 토큰(영문+숫자)이 표시됩니다. 그걸 복사하세요.

       ※ 처음엔 'Test(Explorer)' 등급으로 발급됩니다. 실제 광고 '생성'까지 하려면
         같은 API 센터에서 'Basic 액세스'를 신청해야 하고, 승인에 1~3영업일 걸립니다.
         (데이터 '조회'만이면 Test 등급으로도 됩니다.)
       ※ 토큰 복사 시 대문자 I(아이)와 소문자 l(엘)을 절대 헷갈리지 마세요. 흔한 오류입니다.""")
    tok = ask("개발자 토큰을 붙여넣으세요 (비우면 건너뜀)")
    if not tok:
        c.warn("토큰 미입력 — 승인 후 다시 실행하세요.")
        return
    if len(tok) != 22:
        c.warn(f"토큰 길이가 22자가 아닙니다({len(tok)}자). 오타 가능 — 그래도 저장합니다.")
    c.set_env_value("GOOGLE_ADS_DEVELOPER_TOKEN", tok)
    c.ok(".env 에 개발자 토큰 저장")


# --- Phase 3: GCP project + billing + Ads API (AUTOMATED) --------------------
def phase_gcp() -> None:
    c.step("3) GCP 프로젝트 + Google Ads API  —  [자동] gcloud로 생성·결제연결·활성화")
    gcloud = c.ensure_gcloud_on_path()
    if not gcloud:
        c.err("gcloud 가 없습니다. 먼저: python install_gcloud.py")
        return

    current = c.run([gcloud, "config", "get-value", "project"], echo=False).stdout.strip()
    project = ""
    if current and current != "(unset)":
        c.info(f"현재 gcloud 기본 프로젝트: {current}")
        if confirm(f"이 프로젝트({current})를 사용할까요?"):
            project = current
    if not project:
        suggested = ask("새 프로젝트 ID를 입력하세요 (소문자/숫자/하이픈, 전역 고유)",
                        default="ads-demandgen-setup")
        cp = c.run([gcloud, "projects", "create", suggested])
        if cp.returncode != 0 and "already exists" not in cp.stdout.lower():
            c.report_failure("프로젝트 생성", cp.stdout)
            return
        project = suggested
        c.run([gcloud, "config", "set", "project", project])
        c.ok(f"프로젝트 준비됨: {project}")
    c.set_env_value("GOOGLE_PROJECT_ID", project)

    # Billing: check, and link if needed.
    bill = c.run([gcloud, "billing", "projects", "describe", project,
                  "--format=value(billingEnabled)"], echo=False)
    if bill.returncode == 0 and bill.stdout.strip().lower() == "true":
        c.ok("결제 이미 연결됨")
    else:
        accts = c.run([gcloud, "billing", "accounts", "list",
                       "--format=value(name,displayName)"], echo=False)
        rows = [l for l in accts.stdout.strip().splitlines() if l.strip()]
        if rows:
            c.info("사용 가능한 결제 계정:")
            for i, r in enumerate(rows):
                c.info(f"  [{i}] {r}")
            pick = ask("연결할 결제 계정 번호 (없으면 Enter → 콘솔에서 수동)", default="")
            if pick.isdigit() and int(pick) < len(rows):
                acct_id = rows[int(pick)].split()[0].replace("billingAccounts/", "")
                lk = c.run([gcloud, "billing", "projects", "link", project,
                            f"--billing-account={acct_id}"])
                if lk.returncode == 0:
                    c.ok("결제 연결 완료")
                else:
                    c.report_failure("결제 연결", lk.stdout)
            else:
                c.warn(f"결제 수동 연결 필요: {BILLING_URL}")
                c.open_url(BILLING_URL)
        else:
            c.warn("연결할 결제 계정이 없습니다. 아래 순서로 하나 만든 뒤 다시 실행하세요:")
            print(f"""       [1] 주소창에 입력: {BILLING_URL}
       [2] "결제 계정 만들기" 클릭 → 국가=대한민국, 통화=KRW
       [3] 개인/사업자 선택, 이름·주소 입력
       [4] 신용/체크카드 등록 (신규는 보통 무료 크레딧 제공, Cloud Run은 실제 청구 거의 없음)
       [5] 만든 결제 계정을 이 프로젝트에 연결 → 이 스크립트를 다시 실행""")
            c.open_url(BILLING_URL)

    # Enable the Google Ads API.
    en = c.run([gcloud, "services", "enable", "googleads.googleapis.com", "--project", project])
    if en.returncode == 0:
        c.ok("Google Ads API 활성화 완료")
    else:
        c.report_failure("Ads API 활성화", en.stdout)


# --- Phase 4: OAuth client JSON (semi) --------------------------------------
def phase_oauth_client() -> None:
    c.step("4) OAuth 클라이언트 JSON  —  [반자동] 콘솔에서 생성 후 파일만 지정")
    if c.OAUTH_CLIENT_FILE.exists():
        c.ok(f"이미 있습니다: {c.OAUTH_CLIENT_FILE}")
        return
    if confirm("사용자 인증 정보(Credentials) 페이지를 열어 드릴까요?"):
        c.open_url(CREDENTIALS_URL)
    print(f"""       ── 아래 순서대로 하세요 (조금 길지만 그대로 따라오면 됩니다) ──

       [1] 주소창에 입력: {CREDENTIALS_URL}
             상단에 위에서 만든/고른 프로젝트가 선택돼 있는지 확인하세요.
       [2] 처음이면 'OAuth 동의 화면(OAuth consent screen)'을 먼저 만들라고 나옵니다:
             - User type(사용자 유형) = External(외부) 선택 → 만들기
             - 앱 이름, 사용자 지원 이메일, 개발자 이메일만 채우고 저장·계속
             - ★'테스트 사용자(Test users)'에 본인 구글 이메일을 반드시 추가하세요.
               (이걸 빼먹으면 나중에 로그인 때 '액세스 차단됨(403)'이 뜹니다 — 아주 흔한 실수)
       [3] 왼쪽 메뉴 '사용자 인증 정보' → 상단 '+ 사용자 인증 정보 만들기'
             → 'OAuth 클라이언트 ID' 선택
       [4] 애플리케이션 유형 = '데스크톱 앱' 선택 → 이름 아무거나 → '만들기'
       [5] 뜨는 창에서 'JSON 다운로드' 버튼을 눌러 파일을 받습니다.
       [6] 받은 JSON 파일의 '전체 경로'를 아래에 붙여넣으세요.
             (윈도우: 파일에서 Shift+마우스 우클릭 → '경로로 복사')""")
    src = ask("다운로드한 OAuth 클라이언트 JSON 경로 (비우면 건너뜀)").strip('"')
    if not src:
        c.warn("건너뜀 — 나중에 다시 실행하세요.")
        return
    p = Path(src)
    if not p.exists():
        c.err(f"파일을 찾을 수 없습니다: {p}")
        return
    c.CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(p, c.OAUTH_CLIENT_FILE)
    c.ok(f"복사 완료 → {c.OAUTH_CLIENT_FILE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["0", "1", "2", "3", "4"], help="특정 단계만 실행")
    args = ap.parse_args()

    phases = {"0": phase_account, "1": phase_mcc, "2": phase_token,
              "3": phase_gcp, "4": phase_oauth_client}
    c.step("Google Ads 가이드 셋업 마법사")
    c.info("가능한 건 자동으로, 웹 전용(0·1·2)은 페이지를 열고 안내 + 입력값을 검증합니다.")
    c.info("초보자용 전체 매뉴얼: references/beginner-setup-guide.md")
    if args.only:
        phases[args.only]()
    else:
        for key in ("0", "1", "2", "3", "4"):
            phases[key]()

    c.step("다음 단계")
    c.info("여기까지 되면 나머지는 원커맨드로: python bootstrap.py")
    c.info("(OAuth 동의 → Cloud Run 배포 → Claude Code 등록 → 디멘드젠 검증)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
