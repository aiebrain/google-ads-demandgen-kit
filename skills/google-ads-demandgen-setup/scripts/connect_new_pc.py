#!/usr/bin/env python
"""Connect a SECOND/other PC to the already-deployed Demand Gen MCP server.

No cloud work here — the server already exists. This just registers the same
HTTPS endpoint + bearer token in this PC's Claude Code and confirms it responds.

Credentials are read (in priority order) from:
  1. --url / --token command-line args
  2. MCP_URL / MCP_BEARER_TOKEN environment variables
  3. connection.json in the config dir (synced via claude-config-cloud-sync)

Usage:
    python connect_new_pc.py
    python connect_new_pc.py --url https://xxx.run.app/mcp --token <bearer>
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

import _common as c


def _resolve(args) -> tuple[str, str]:
    url = args.url or os.environ.get("MCP_URL", "")
    token = args.token or os.environ.get("MCP_BEARER_TOKEN", "")
    if (not url or not token) and c.CONNECTION_FILE.exists():
        conn = json.loads(c.CONNECTION_FILE.read_text(encoding="utf-8"))
        url = url or conn.get("mcp_url", "")
        token = token or conn.get("bearer_token", "")
    return url.strip(), token.strip()


def _healthcheck(mcp_url: str) -> bool:
    # mcp_url ends with /mcp; the health endpoint is /healthz on the same host.
    base = mcp_url[:-len("/mcp")] if mcp_url.endswith("/mcp") else mcp_url.rstrip("/")
    health = base + "/healthz"
    try:
        with urllib.request.urlopen(health, timeout=15) as resp:
            body = resp.read().decode("utf-8", "ignore")
            if resp.status == 200 and "true" in body.lower():
                c.ok(f"서버 응답 정상: {health}")
                return True
            c.warn(f"예상치 못한 health 응답({resp.status}): {body[:120]}")
            return False
    except urllib.error.URLError as e:
        c.err(f"서버에 연결할 수 없습니다: {health} ({e})")
        c.info("URL 이 맞는지, 서버가 배포·실행 중인지 확인하세요.")
        return False


def _register(mcp_url: str, token: str) -> bool:
    claude = c.find_exe("claude")
    manual = (f'claude mcp add --transport http --scope user google_ads_direct {mcp_url} '
              f'--header "Authorization: Bearer {token}"')
    if not claude:
        c.warn("claude CLI 를 찾지 못했습니다. 아래 명령을 수동 실행하세요:")
        c.info(manual)
        return False
    c.run([claude, "mcp", "remove", "google_ads_direct", "--scope", "user"],
          unset=["CLAUDECODE"], echo=False)
    cp = c.run([claude, "mcp", "add", "--transport", "http", "--scope", "user",
                "google_ads_direct", mcp_url, "--header", f"Authorization: Bearer {token}"],
               unset=["CLAUDECODE"])
    if cp.returncode != 0:
        c.report_failure("Claude Code 등록", cp.stdout)
        c.info("수동 등록 명령:")
        c.info(manual)
        return False
    c.ok("Claude Code 에 등록 완료 (scope: user)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="MCP URL (…/mcp)")
    ap.add_argument("--token", help="베어러 토큰")
    args = ap.parse_args()

    c.step("연결 정보 확인")
    url, token = _resolve(args)
    if not url or not token:
        c.err("MCP URL 또는 베어러 토큰을 찾을 수 없습니다.")
        c.info("--url/--token 인자, 또는 MCP_URL/MCP_BEARER_TOKEN 환경변수, "
               f"또는 {c.CONNECTION_FILE} 중 하나로 제공하세요.")
        return 2
    c.ok(f"URL: {url}")

    c.step("서버 상태 확인")
    healthy = _healthcheck(url)

    c.step("Claude Code 등록")
    registered = _register(url, token)

    c.step("완료")
    if registered and healthy:
        c.ok("이 PC 연결 완료. Claude Code 에서 '내 구글애즈 계정 목록 보여줘' 로 확인하세요.")
        c.info("디멘드젠 광고 설정도 첫 PC와 동일하게 가능합니다 (같은 서버를 쓰므로).")
        return 0
    c.warn("일부 단계가 완료되지 않았습니다. 위 안내를 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
