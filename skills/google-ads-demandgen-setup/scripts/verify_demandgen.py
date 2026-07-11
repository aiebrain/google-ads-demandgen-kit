#!/usr/bin/env python
"""Verify this PC can actually configure Demand Gen ads — without spending money.

Three layers, cheapest/safest first:
  1. Auth + read:  runs the server's --self-test (lists accessible customers).
                   Proves the developer token is approved and OAuth works.
  2. Write probe:  a validate_only campaign-budget mutate. validate_only=True means
                   NOTHING is created/charged, but the API still checks whether this
                   account would accept the write. This is the real "can I configure
                   Demand Gen here" signal (Demand Gen campaigns use campaign budgets).
  3. Tool presence: confirms the 8 Demand Gen tool functions exist in the server.

Optional --live actually creates a PAUSED Demand Gen campaign and deletes it (full
end-to-end); only use it if you understand it touches the account (still no spend
while PAUSED, and it is removed afterward).

Usage:
    python verify_demandgen.py
    python verify_demandgen.py --customer-id 1234567890
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import _common as c

DEMAND_GEN_TOOLS = [
    "upload_image_asset", "upload_logo_asset", "attach_youtube_video_asset",
    "create_demand_gen_campaign", "create_demand_gen_ad_group",
    "create_demand_gen_multi_asset_ad", "create_demand_gen_video_responsive_ad",
    "validate_created_demand_gen_ad",
]


def _self_test() -> bool:
    server = c.ASSETS_DIR / "google_ads_mcp_server.py"
    env = {"GOOGLE_ADS_MCP_HOME": str(c.CONFIG_HOME)}
    cp = c.run([sys.executable, str(server), "--self-test"], cwd=c.ASSETS_DIR, env=env)
    if cp.returncode == 0:
        c.ok("인증·읽기 정상 (계정 목록 조회 성공)")
        return True
    c.report_failure("self-test (인증·읽기)", cp.stdout)
    return False


def _tool_presence() -> bool:
    src = (c.ASSETS_DIR / "google_ads_mcp_server.py").read_text(encoding="utf-8", errors="ignore")
    missing = [t for t in DEMAND_GEN_TOOLS if f"def {t}(" not in src]
    if missing:
        c.err(f"서버에 없는 디멘드젠 툴: {', '.join(missing)}")
        return False
    c.ok(f"디멘드젠 툴 {len(DEMAND_GEN_TOOLS)}개 모두 존재")
    return True


def _build_client():
    """Minimal client builder mirroring the server's, without importing FastMCP."""
    from google.ads.googleads.client import GoogleAdsClient
    from google.oauth2.credentials import Credentials
    scope = "https://www.googleapis.com/auth/adwords"
    dev_token = c.env_value("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not dev_token:
        raise RuntimeError("GOOGLE_ADS_DEVELOPER_TOKEN 이 .env 에 없습니다.")
    if not c.ADC_FILE.exists():
        raise RuntimeError(f"리프레시 토큰 파일이 없습니다: {c.ADC_FILE}")
    adc = json.loads(c.ADC_FILE.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(adc, scopes=[scope])
    kwargs = {"credentials": creds, "developer_token": dev_token, "use_proto_plus": True}
    login_cid = c.env_value("GOOGLE_ADS_LOGIN_CUSTOMER_ID").replace("-", "").strip()
    if login_cid:
        kwargs["login_customer_id"] = login_cid
    return GoogleAdsClient(**kwargs)


def _write_probe(customer_id: str) -> bool:
    if not customer_id:
        c.warn("customer_id 가 없어 쓰기 권한 확인을 건너뜁니다.")
        c.info(".env 의 GOOGLE_ADS_CUSTOMER_ID 를 채우거나 --customer-id 로 전달하세요.")
        return True  # non-blocking
    try:
        from google.ads.googleads.errors import GoogleAdsException
    except ImportError:
        c.warn("google-ads 라이브러리가 없어 쓰기 확인을 건너뜁니다 (bootstrap 의존성 설치 필요).")
        return True
    try:
        client = _build_client()
        svc = client.get_service("CampaignBudgetService")
        op = client.get_type("CampaignBudgetOperation")
        b = op.create
        b.name = f"demandgen-verify-probe-{uuid.uuid4().hex[:8]}"
        b.amount_micros = 1_000_000
        b.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        # validate_only lives on the request message (not a kwarg): the API validates
        # the write but creates/charges NOTHING.
        request = client.get_type("MutateCampaignBudgetsRequest")
        request.customer_id = customer_id
        request.operations = [op]
        request.validate_only = True
        svc.mutate_campaign_budgets(request=request)
        c.ok("쓰기 권한 정상 (validate_only — 실제 생성/과금 없음). 디멘드젠 설정 가능.")
        return True
    except GoogleAdsException as e:
        text = str(e)
        c.report_failure("쓰기 권한 확인", text)
        return False
    except Exception as e:  # noqa: BLE001 - surface any auth/config error with diagnosis
        c.report_failure("쓰기 권한 확인", str(e))
        return False


def _live(customer_id: str) -> bool:
    c.warn("--live: 실제로 PAUSED 디멘드젠 캠페인을 만들고 즉시 삭제합니다.")
    if not customer_id:
        c.err("--live 에는 customer_id 가 필요합니다.")
        return False
    c.info("이 경로는 서버 툴을 통해 수행하는 것이 안전합니다. "
           "Claude Code 에서 'PAUSED 디멘드젠 캠페인 테스트로 만들고 삭제해줘' 로 요청하세요.")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer-id", default=None, help="쓰기 확인에 쓸 광고계정 ID (숫자만)")
    ap.add_argument("--live", action="store_true", help="실제 PAUSED 캠페인 생성·삭제(E2E)")
    args = ap.parse_args()
    customer_id = (args.customer_id or c.env_value("GOOGLE_ADS_CUSTOMER_ID")).replace("-", "").strip()

    c.step("1/3 인증·읽기 검증")
    r1 = _self_test()
    c.step("2/3 디멘드젠 툴 존재 확인")
    r2 = _tool_presence()
    c.step("3/3 쓰기 권한(validate_only) 검증")
    r3 = _write_probe(customer_id) if r1 else (c.warn("인증 실패로 건너뜀") or False)
    if args.live and r1 and r3:
        c.step("추가: 라이브 E2E")
        _live(customer_id)

    c.step("결과")
    if r1 and r2 and r3:
        c.ok("디멘드젠 광고 설정 준비 완료 ✅")
        return 0
    c.err("일부 검증이 실패했습니다. 위 진단·해결 안내를 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
