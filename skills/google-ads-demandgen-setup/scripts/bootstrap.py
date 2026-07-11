#!/usr/bin/env python
"""One-command Demand Gen setup for the FIRST PC.

Chains the whole thing so a new user runs one command and ends with a working,
Demand-Gen-capable MCP endpoint registered in Claude Code:

  preflight -> deps -> ensure .env -> OAuth (refresh token) -> bearer token
           -> deploy to Cloud Run -> register in Claude Code -> verify

Every step captures output and, on failure, prints a diagnosis + fix (see
_common.diagnose) instead of a bare traceback. Re-running is safe: steps that are
already done are detected and skipped.

Usage:
    python bootstrap.py                 # full first-time setup
    python bootstrap.py --reauth        # force a fresh OAuth consent (new refresh token)
    python bootstrap.py --skip-verify   # deploy+register but skip the live self-test
    python bootstrap.py --region us-central1
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import _common as c
import install_gcloud
import preflight


def _confirm(prompt: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"       {prompt} [y/N]: ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _ensure_gcloud(assume_yes: bool) -> None:
    """Make sure gcloud is installed and reachable in THIS session. Auto-installs
    (per-OS) with confirmation, then injects its bin dir into PATH so the later
    deploy step finds it without opening a new terminal."""
    if c.ensure_gcloud_on_path():
        c.ok("gcloud 사용 가능")
        return
    c.warn("gcloud CLI 가 없습니다.")
    if not _confirm("gcloud 를 지금 자동 설치할까요?", assume_yes):
        c.info("수동 설치: https://cloud.google.com/sdk/docs/install → 설치 후 bootstrap 재실행.")
        raise SystemExit(2)
    # Call the installer in-process (so its PATH injection persists here). Pass an
    # explicit empty argv so it doesn't parse bootstrap's own flags.
    code = install_gcloud.main([])
    if code != 0 or not c.ensure_gcloud_on_path():
        c.err("gcloud 설치 후에도 이 세션에서 사용할 수 없습니다.")
        c.info("새 터미널을 열고 `gcloud auth login` 후 bootstrap 을 다시 실행하세요.")
        raise SystemExit(2)
    c.ok("gcloud 설치·연결 완료")


def _run_preflight_gate() -> None:
    results = preflight.collect()
    hard_fail = [r for r in results if r["status"] == "fail"]
    # These are the ones bootstrap itself creates later; don't block on them.
    creatable = {".env 파일", "리프레시 토큰(google_ads_adc.json)", "베어러 토큰", ".env 값"}
    blocking = [r for r in hard_fail if r["name"] not in creatable]
    for r in results:
        (c.ok if r["status"] == "ok" else c.warn if r["status"] == "warn" else c.err)(
            f"{r['name']}: {r['detail']}")
        if r["status"] != "ok" and r.get("fix"):
            c.info("→ " + r["fix"])
    if blocking:
        c.err("아래 필수 항목을 먼저 해결한 뒤 다시 실행하세요:")
        for r in blocking:
            c.info(f"- {r['name']}: {r.get('fix', '')}")
        raise SystemExit(2)


def _ensure_env() -> None:
    c.CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    if not c.ENV_FILE.exists():
        template = Path(__file__).resolve().parent.parent / "config" / ".env.example"
        if template.exists():
            shutil.copyfile(template, c.ENV_FILE)
            c.warn(f".env 를 생성했습니다: {c.ENV_FILE}")
            c.info("이 파일을 열어 개발자 토큰·프로젝트 ID·MCC/고객 ID 를 채운 뒤 다시 실행하세요.")
            raise SystemExit(3)
        raise SystemExit(f".env 도, 템플릿({template})도 없습니다.")
    # Validate that required values are actually filled.
    missing = [k for k in ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_PROJECT_ID")
               if not c.env_value(k) or c.env_value(k).startswith("your")]
    if missing:
        c.err(f".env 의 필수 값이 비어 있습니다: {', '.join(missing)}")
        c.info(f"{c.ENV_FILE} 를 채운 뒤 다시 실행하세요.")
        raise SystemExit(3)
    c.ok(".env 확인됨")


def _install_deps() -> None:
    req = c.ASSETS_DIR / "requirements.txt"
    cp = c.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)])
    if cp.returncode != 0:
        c.report_failure("의존성 설치", cp.stdout)
        raise SystemExit(4)
    c.ok("파이썬 의존성 설치 완료")


def _ensure_oauth(reauth: bool) -> None:
    if c.ADC_FILE.exists() and not reauth:
        c.ok(f"리프레시 토큰 존재: {c.ADC_FILE}")
        return
    if not c.OAUTH_CLIENT_FILE.exists():
        c.err(f"OAuth 클라이언트 JSON이 없습니다: {c.OAUTH_CLIENT_FILE}")
        c.info("Google Cloud Console → APIs & Services → Credentials → "
               "OAuth client ID (Application type: Desktop app) → JSON 다운로드")
        c.info(f"받은 파일을 {c.OAUTH_CLIENT_FILE} 로 저장한 뒤 다시 실행하세요.")
        raise SystemExit(5)
    c.info("브라우저(또는 출력되는 URL)에서 본인 구글 계정으로 동의하세요. 1회만 필요합니다.")
    # oauth_setup.py is interactive (opens a consent URL), so DON'T capture stdout —
    # let it talk to the terminal directly.
    env = {"GOOGLE_ADS_MCP_HOME": str(c.CONFIG_HOME),
           "GOOGLE_ADS_OAUTH_CLIENT_JSON": str(c.OAUTH_CLIENT_FILE)}
    proc = subprocess.run([sys.executable, "oauth_setup.py"], cwd=str(c.ASSETS_DIR),
                          env={**os.environ, **env})
    if proc.returncode != 0 or not c.ADC_FILE.exists():
        c.err("OAuth 동의/토큰 생성 실패.")
        c.info("동의 화면에서 본인 계정을 test user 로 추가했는지, "
               "OAuth consent screen 이 구성됐는지 확인하세요.")
        raise SystemExit(5)
    c.ok(f"리프레시 토큰 생성됨: {c.ADC_FILE}")


def _ensure_bearer() -> str:
    if c.BEARER_FILE.exists() and c.BEARER_FILE.read_text(encoding="utf-8").strip():
        token = c.BEARER_FILE.read_text(encoding="utf-8").strip()
        c.ok("베어러 토큰 존재")
        return token
    token = secrets.token_urlsafe(32)
    c.BEARER_FILE.write_text(token, encoding="utf-8")
    c.ok(f"베어러 토큰 생성됨: {c.BEARER_FILE}")
    return token


def _deploy(region: str, bearer: str) -> dict:
    env = {
        "GOOGLE_ADS_MCP_HOME": str(c.CONFIG_HOME),
        "MCP_BEARER_TOKEN": bearer,           # passed via env so no secret is written into assets/
        "REGION": region,
    }
    project = c.env_value("GOOGLE_PROJECT_ID")
    if project:
        env["PROJECT_ID"] = project
    c.info("Cloud Run 배포 중 (API 활성화·시크릿 생성·빌드 — 몇 분 걸릴 수 있습니다)...")
    cp = c.run([sys.executable, "deploy_cloud_run.py"], cwd=c.ASSETS_DIR, env=env)
    print(cp.stdout)
    if cp.returncode != 0:
        c.report_failure("Cloud Run 배포", cp.stdout)
        raise SystemExit(6)
    if not c.LAST_DEPLOY_FILE.exists():
        c.report_failure("배포 결과 확인", cp.stdout)
        raise SystemExit(6)
    data = json.loads(c.LAST_DEPLOY_FILE.read_text(encoding="utf-8"))
    c.ok(f"배포 완료: {data.get('mcp_url')}")
    return data


def _save_connection(deploy: dict, bearer: str) -> None:
    conn = {
        "mcp_url": deploy.get("mcp_url"),
        "service_url": deploy.get("service_url"),
        "region": deploy.get("region"),
        "project_id": deploy.get("project_id"),
        "bearer_token": bearer,
    }
    c.CONNECTION_FILE.write_text(json.dumps(conn, ensure_ascii=False, indent=2), encoding="utf-8")
    c.ok(f"연결 정보 저장: {c.CONNECTION_FILE}")
    c.info("이 파일에는 베어러 토큰(=광고비 권한)이 들어 있습니다. 안전하게 보관/동기화하세요.")


def _register(mcp_url: str, bearer: str) -> None:
    claude = c.find_exe("claude")
    if not claude:
        c.warn("claude CLI 를 찾지 못해 자동 등록을 건너뜁니다.")
        c.info("다음 명령을 수동 실행하세요:")
        c.info(f'claude mcp add --transport http --scope user google_ads_direct {mcp_url} '
               f'--header "Authorization: Bearer {bearer}"')
        return
    # Remove any stale registration first so re-runs update cleanly. CLAUDECODE is
    # unset so these calls work even when bootstrap runs inside a Claude Code session.
    c.run([claude, "mcp", "remove", "google_ads_direct", "--scope", "user"],
          unset=["CLAUDECODE"], echo=False)
    cp = c.run([claude, "mcp", "add", "--transport", "http", "--scope", "user",
                "google_ads_direct", mcp_url,
                "--header", f"Authorization: Bearer {bearer}"], unset=["CLAUDECODE"])
    if cp.returncode != 0:
        c.report_failure("Claude Code 등록", cp.stdout)
        c.info("위 명령을 수동으로 실행해도 됩니다.")
        return
    c.ok("Claude Code 에 google_ads_direct 등록 완료 (scope: user)")


def _verify(skip: bool) -> None:
    if skip:
        c.warn("검증(self-test)을 건너뜁니다 (--skip-verify).")
        return
    verify = Path(__file__).resolve().parent / "verify_demandgen.py"
    proc = subprocess.run([sys.executable, str(verify)])
    if proc.returncode != 0:
        c.warn("검증에서 문제가 감지됐습니다. 위 출력을 확인하세요.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reauth", action="store_true", help="OAuth 동의를 새로 받아 리프레시 토큰 재발급")
    ap.add_argument("--skip-verify", action="store_true", help="배포·등록 후 self-test 생략")
    ap.add_argument("--region", default=None, help="Cloud Run 리전 (기본 asia-northeast3)")
    ap.add_argument("--yes", "-y", action="store_true", help="설치 등 확인 프롬프트를 자동 승인")
    args = ap.parse_args()
    region = args.region or "asia-northeast3"

    c.step("1/9 gcloud CLI 확인·설치")
    _ensure_gcloud(args.yes)
    c.step("2/9 환경 진단")
    _run_preflight_gate()
    c.step("3/9 .env 확인")
    _ensure_env()
    c.step("4/9 파이썬 의존성 설치")
    _install_deps()
    c.step("5/9 OAuth 리프레시 토큰")
    _ensure_oauth(args.reauth)
    c.step("6/9 베어러 토큰")
    bearer = _ensure_bearer()
    c.step(f"7/9 Cloud Run 배포 (region={region})")
    deploy = _deploy(region, bearer)
    _save_connection(deploy, bearer)
    c.step("8/9 Claude Code 등록")
    _register(deploy["mcp_url"], bearer)
    c.step("9/9 디멘드젠 사용 가능 검증")
    _verify(args.skip_verify)

    c.step("완료")
    c.ok("이 PC에서 디멘드젠 광고 설정 준비가 끝났습니다.")
    c.info(f"MCP URL : {deploy.get('mcp_url')}")
    c.info("다른 PC에서 쓰려면 connect_new_pc.py 에 위 URL 과 베어러 토큰을 주면 됩니다.")
    c.info("Claude Code 에서 '내 구글애즈 계정 목록 보여줘' 로 먼저 읽기부터 확인하세요.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        c.report_failure("명령 실행", e.output or "")
        raise SystemExit(1)
