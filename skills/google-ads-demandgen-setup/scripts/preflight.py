#!/usr/bin/env python
"""Diagnose whether this PC is ready to build/deploy the Demand Gen setup.

Runs a series of non-destructive checks and prints, for each, OK / warning /
missing plus a concrete fix. Exit code 0 means "ready to deploy"; non-zero means
at least one hard requirement is missing (the summary lists what to fix).

Usage:
    python preflight.py            # full check
    python preflight.py --json     # machine-readable result for other scripts
"""
from __future__ import annotations

import argparse
import json
import sys

import _common as c


def _py_ok() -> tuple[bool, str]:
    v = sys.version_info
    if (v.major, v.minor) >= (3, 10):
        return True, f"Python {v.major}.{v.minor}.{v.micro}"
    return False, f"Python {v.major}.{v.minor} — 3.10 이상이 필요합니다 (https://www.python.org/downloads/)"


def _gcloud_checks(results: list[dict]) -> None:
    gcloud = c.ensure_gcloud_on_path()
    if not gcloud:
        results.append({"name": "gcloud CLI", "status": "fail",
                        "detail": "설치되지 않았거나 PATH에 없습니다.",
                        "fix": "install_gcloud.py 로 자동 설치하거나 "
                               "https://cloud.google.com/sdk/docs/install (bootstrap 이 자동 설치 제안)."})
        return
    results.append({"name": "gcloud CLI", "status": "ok", "detail": gcloud})

    acct = c.run([gcloud, "auth", "list", "--filter=status:ACTIVE",
                  "--format=value(account)"], echo=False)
    active = acct.stdout.strip().splitlines()[0] if acct.stdout.strip() else ""
    if active:
        results.append({"name": "gcloud 로그인", "status": "ok", "detail": active})
    else:
        results.append({"name": "gcloud 로그인", "status": "fail",
                        "detail": "활성 계정이 없습니다.",
                        "fix": "gcloud auth login 실행."})

    proj = c.run([gcloud, "config", "get-value", "project"], echo=False)
    project = proj.stdout.strip()
    if project and project != "(unset)":
        results.append({"name": "GCP 프로젝트", "status": "ok", "detail": project})
        # Billing check is best-effort (needs the billing API / beta component).
        bill = c.run([gcloud, "billing", "projects", "describe", project,
                      "--format=value(billingEnabled)"], echo=False)
        if bill.returncode == 0 and bill.stdout.strip().lower() == "true":
            results.append({"name": "결제 연결", "status": "ok", "detail": "billingEnabled=True"})
        elif bill.returncode == 0:
            results.append({"name": "결제 연결", "status": "fail",
                            "detail": "이 프로젝트에 결제가 연결되지 않았습니다.",
                            "fix": "https://console.cloud.google.com/billing 에서 결제 계정을 연결하세요."})
        else:
            results.append({"name": "결제 연결", "status": "warn",
                            "detail": "자동 확인 실패(billing 권한/컴포넌트 없음).",
                            "fix": "콘솔에서 결제 연결 여부를 직접 확인하세요."})
    else:
        results.append({"name": "GCP 프로젝트", "status": "fail",
                        "detail": "기본 프로젝트가 설정되지 않았습니다.",
                        "fix": "gcloud config set project <PROJECT_ID>."})


def _file_checks(results: list[dict]) -> None:
    # Config dir and its contents.
    if c.ENV_FILE.exists():
        missing = [k for k in ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_PROJECT_ID")
                   if not c.env_value(k) or c.env_value(k).startswith("your")]
        if missing:
            results.append({"name": ".env 값", "status": "fail",
                            "detail": f"채워지지 않은 필수 값: {', '.join(missing)}",
                            "fix": f"{c.ENV_FILE} 를 열어 값을 채우세요 (config/.env.example 참고)."})
        else:
            results.append({"name": ".env 값", "status": "ok", "detail": str(c.ENV_FILE)})
    else:
        results.append({"name": ".env 파일", "status": "fail",
                        "detail": f"{c.ENV_FILE} 없음.",
                        "fix": "bootstrap.py 가 config/.env.example 를 복사해 만듭니다(또는 수동 복사)."})

    checks = [
        ("OAuth 클라이언트 JSON", c.OAUTH_CLIENT_FILE,
         "Google Cloud Console에서 OAuth 클라이언트(Desktop app) JSON을 받아 여기에 저장하세요.", "fail"),
        ("리프레시 토큰(google_ads_adc.json)", c.ADC_FILE,
         "bootstrap.py 가 oauth_setup 을 실행해 생성합니다 (1회 브라우저 동의 필요).", "warn"),
        ("베어러 토큰", c.BEARER_FILE,
         "bootstrap.py 가 무작위 토큰을 자동 생성합니다.", "warn"),
    ]
    for name, path, fix, missing_status in checks:
        if path.exists():
            results.append({"name": name, "status": "ok", "detail": str(path)})
        else:
            results.append({"name": name, "status": missing_status,
                            "detail": f"{path} 없음.", "fix": fix})


def _asset_check(results: list[dict]) -> None:
    server = c.ASSETS_DIR / "google_ads_mcp_server.py"
    if server.exists():
        results.append({"name": "서버 자산(google-ads-direct-mcp)", "status": "ok",
                        "detail": str(c.ASSETS_DIR)})
    else:
        results.append({"name": "서버 자산(google-ads-direct-mcp)", "status": "fail",
                        "detail": f"{server} 를 찾을 수 없습니다.",
                        "fix": "google-ads-direct-mcp 스킬이 설치돼 있어야 합니다(이 스킬이 그 자산을 재사용)."})


def collect() -> list[dict]:
    results: list[dict] = []
    py_ok, py_detail = _py_ok()
    results.append({"name": "Python 3.10+", "status": "ok" if py_ok else "fail",
                    "detail": py_detail,
                    "fix": "https://www.python.org/downloads/ (설치 시 'Add to PATH' 체크)"})
    _asset_check(results)
    _gcloud_checks(results)
    _file_checks(results)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    args = ap.parse_args()

    results = collect()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        c.step("환경 진단 (preflight)")
        for r in results:
            line = f"{r['name']}: {r['detail']}"
            if r["status"] == "ok":
                c.ok(line)
            elif r["status"] == "warn":
                c.warn(line)
                if r.get("fix"):
                    c.info(r["fix"])
            else:
                c.err(line)
                if r.get("fix"):
                    c.info("→ " + r["fix"])

    if not args.json:
        c.step("참고")
        c.info("개발자 토큰이 Test 등급이면 실계정 디멘드젠 설정에서 막힙니다(DEVELOPER_TOKEN_NOT_APPROVED).")
        c.info("API 센터(https://ads.google.com/aw/apicenter)에서 Basic 승인을 미리 신청해 두세요.")

    fails = [r for r in results if r["status"] == "fail"]
    if not args.json:
        c.step("요약")
        if fails:
            c.err(f"해결해야 할 항목 {len(fails)}개:")
            for r in fails:
                c.info(f"- {r['name']}: {r.get('fix', '')}")
        else:
            c.ok("배포 준비 완료. bootstrap.py 를 실행하세요.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
