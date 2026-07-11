#!/usr/bin/env python
"""Link a regular ad account under your manager (MCC) account, with guidance.

Google Ads account linking is invite -> accept. This script:
  1. Sends the invitation from the manager (MCC) to the client ad account via API.
  2. Tries to accept it via API. If your developer token is only Test/Explorer
     level, the API accept is blocked (DEVELOPER_TOKEN_NOT_APPROVED) — the script
     then prints the exact Google Ads UI steps so YOU can accept it by hand (UI
     acceptance works regardless of token level).
  3. Re-run with --check anytime to see the current status; once ACTIVE it offers
     to switch the config to manager-based access and hints the redeploy.

You cannot CREATE a manager (MCC) account via the API — that is a one-time signup
at ads.google.com. This script assumes you already have one (this project: 1234567890).

Usage:
    python link_account.py --manager 1234567890 --client 9876543210
    python link_account.py --check --manager 1234567890 --client 9876543210
    python link_account.py --manager 1234567890 --client 9876543210 --set-login
"""
from __future__ import annotations

import argparse
import json
import sys

import _common as c

MCC_SETUP_URL = "https://ads.google.com/home/tools/manager-accounts"
API_CENTER_URL = "https://ads.google.com/aw/apicenter"


def _build_client(login: str | None = None):
    from google.ads.googleads.client import GoogleAdsClient
    from google.oauth2.credentials import Credentials
    scope = "https://www.googleapis.com/auth/adwords"
    dev = c.env_value("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not dev:
        raise RuntimeError("GOOGLE_ADS_DEVELOPER_TOKEN 이 .env 에 없습니다.")
    if not c.ADC_FILE.exists():
        raise RuntimeError(f"리프레시 토큰 파일이 없습니다: {c.ADC_FILE}")
    adc = json.loads(c.ADC_FILE.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(adc, scopes=[scope])
    kw = {"credentials": creds, "developer_token": dev, "use_proto_plus": True}
    if login:
        kw["login_customer_id"] = login
    return GoogleAdsClient(**kw)


def _status(manager: str, client_id: str):
    """Return (resource_name, status_name, manager_link_id) for the link, or (None, None, None)."""
    cl = _build_client(login=manager)
    svc = cl.get_service("GoogleAdsService")
    q = ("SELECT customer_client_link.resource_name, customer_client_link.status, "
         "customer_client_link.manager_link_id, customer_client_link.client_customer "
         "FROM customer_client_link "
         f"WHERE customer_client_link.client_customer = 'customers/{client_id}'")
    for b in svc.search_stream(customer_id=manager, query=q):
        for r in b.results:
            l = r.customer_client_link
            return l.resource_name, l.status.name, str(l.manager_link_id)
    return None, None, None


def _invite(manager: str, client_id: str) -> str:
    cl = _build_client(login=manager)
    svc = cl.get_service("CustomerClientLinkService")
    op = cl.get_type("CustomerClientLinkOperation")
    op.create.client_customer = f"customers/{client_id}"
    op.create.status = cl.enums.ManagerLinkStatusEnum.PENDING
    resp = svc.mutate_customer_client_link(customer_id=manager, operation=op)
    mid = resp.result.resource_name.split("~")[-1]
    c.ok(f"연결 초대 생성됨 (PENDING). manager_link_id={mid}")
    return mid


def _api_accept(manager: str, client_id: str, mid: str) -> bool:
    from google.ads.googleads.errors import GoogleAdsException
    cl = _build_client(login=None)  # accept as the client account (direct access)
    svc = cl.get_service("CustomerManagerLinkService")
    op = cl.get_type("CustomerManagerLinkOperation")
    op.update.resource_name = f"customers/{client_id}/customerManagerLinks/{manager}~{mid}"
    op.update.status = cl.enums.ManagerLinkStatusEnum.ACTIVE
    op.update_mask.paths.append("status")
    try:
        svc.mutate_customer_manager_link(customer_id=client_id, operations=[op])
        c.ok("API 로 수락 완료 → 연결 ACTIVE")
        return True
    except GoogleAdsException as e:
        text = "; ".join(f"{err.error_code}: {err.message}" for err in e.failure.errors)
        if "NOT_APPROVED" in text or "explorer access" in text.lower():
            c.warn("API 자동 수락이 개발자 토큰 등급(Test/Explorer) 때문에 막혔습니다.")
            return False
        c.report_failure("API 수락", text)
        return False


def _ui_accept_steps(manager: str, client_id: str) -> None:
    m = f"{manager[:3]}-{manager[3:6]}-{manager[6:]}"
    cl = f"{client_id[:3]}-{client_id[3:6]}-{client_id[6:]}"
    c.step("UI 에서 연결 수락하기 (개발자 토큰 등급과 무관하게 지금 가능)")
    print(f"""       초대는 이미 보내졌습니다(PENDING). 광고계정에서 수락만 하면 연결됩니다:

       1. ads.google.com 접속 → 상단 계정 선택기에서 광고계정 [{cl}] 로 전환
       2. 렌치(도구 및 설정) → 설정(Setup) → 액세스 및 보안(Access and security)
       3. "관리자(Managers)" 탭 클릭
       4. 대기 중인 요청에서 관리자 [{m}] 를 찾아 → 수락(Accept)

       수락 후 확인:  python link_account.py --check --manager {manager} --client {client_id}
""")


def _basic_access_note() -> None:
    c.step("Basic 액세스 신청 (실제 캠페인 생성·API 자동화에 필요, 조회는 지금도 가능)")
    print(f"""       Test/Explorer 등급으로는 실제 쓰기(캠페인 생성)와 일부 API 가 막힙니다.
       관리자(MCC)로 API 센터에 접속해 Basic 액세스를 신청하세요 (승인 1~3영업일):
         {API_CENTER_URL}
""")


def _offer_set_login(manager: str, do_it: bool) -> None:
    if do_it:
        env_text = c.ENV_FILE.read_text(encoding="utf-8")
        import re
        new = re.sub(r"(?m)^GOOGLE_ADS_LOGIN_CUSTOMER_ID=.*$",
                     f"GOOGLE_ADS_LOGIN_CUSTOMER_ID={manager}", env_text)
        c.ENV_FILE.write_text(new, encoding="utf-8")
        c.ok(f".env 의 GOOGLE_ADS_LOGIN_CUSTOMER_ID 를 {manager} 로 설정했습니다.")
        c.info("원격 서버에 반영하려면 재배포: python bootstrap.py --yes")
    else:
        c.info(f"MCC 경유로 쓰려면 .env 에 GOOGLE_ADS_LOGIN_CUSTOMER_ID={manager} 설정 후 "
               "python bootstrap.py --yes 로 재배포하세요 (또는 --set-login 옵션 사용).")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manager", help="관리자(MCC) 계정 ID, 숫자만")
    ap.add_argument("--client", help="연결할 광고계정 ID, 숫자만 (기본: .env 의 GOOGLE_ADS_CUSTOMER_ID)")
    ap.add_argument("--check", action="store_true", help="현재 연결 상태만 확인")
    ap.add_argument("--set-login", action="store_true", help="ACTIVE 되면 .env 의 login_customer_id 를 MCC 로 설정")
    args = ap.parse_args()

    manager = (args.manager or "").replace("-", "").strip()
    client_id = (args.client or c.env_value("GOOGLE_ADS_CUSTOMER_ID")).replace("-", "").strip()
    if not manager:
        c.err("관리자(MCC) 계정 ID 가 필요합니다: --manager <ID>")
        c.info(f"관리자 계정이 없다면 먼저 만들어야 합니다(웹 가입, API 불가): {MCC_SETUP_URL}")
        return 2
    if not client_id:
        c.err("연결할 광고계정 ID 가 필요합니다: --client <ID> (또는 .env 의 GOOGLE_ADS_CUSTOMER_ID)")
        return 2

    c.step(f"연결 상태 확인 (MCC {manager} ← 광고계정 {client_id})")
    try:
        rn, status, mid = _status(manager, client_id)
    except Exception as e:  # noqa: BLE001
        c.report_failure("연결 상태 조회", str(e))
        return 1
    c.info(f"현재 상태: {status or '연결 없음'}")

    if status == "ACTIVE":
        c.ok("이미 연결되어 있습니다 (ACTIVE). 데이터 조회가 관리자 계정 통해 가능합니다.")
        _offer_set_login(manager, args.set_login)
        return 0

    if args.check:
        if status == "PENDING":
            c.warn("초대는 보내졌으나 아직 수락되지 않았습니다.")
            _ui_accept_steps(manager, client_id)
        else:
            c.info("연결이 없습니다. --check 없이 실행하면 초대를 생성합니다.")
        return 0

    # Not active, not check-only: ensure an invitation exists, then try to accept.
    if status is None:
        try:
            mid = _invite(manager, client_id)
        except Exception as e:  # noqa: BLE001
            c.report_failure("연결 초대 생성", str(e))
            return 1
    else:
        c.info("이미 대기 중(PENDING)인 초대가 있습니다.")

    if _api_accept(manager, client_id, mid):
        _offer_set_login(manager, args.set_login)
        return 0

    # API accept blocked → guide the user through the UI + Basic access application.
    _ui_accept_steps(manager, client_id)
    _basic_access_note()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
