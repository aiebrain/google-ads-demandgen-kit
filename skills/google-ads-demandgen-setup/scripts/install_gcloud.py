#!/usr/bin/env python
"""Install the Google Cloud CLI (gcloud) automatically, per-OS.

bootstrap.py calls this when gcloud is missing; it can also run standalone. After
installing, it makes gcloud reachable in the current session (PATH injection) and
optionally runs `gcloud auth login` + sets the project from .env, so bootstrap can
continue without opening a fresh terminal.

Strategy per OS:
  Windows : winget (Google.CloudSDK) → fallback: download & launch the official installer
  macOS   : Homebrew cask (google-cloud-sdk) → fallback: official interactive script
  Linux   : official install script (curl https://sdk.cloud.google.com | bash)

Usage:
    python install_gcloud.py            # install + (optional) auth
    python install_gcloud.py --no-auth  # install only
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import _common as c

WINDOWS_INSTALLER_URL = "https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe"
UNIX_INSTALL_SCRIPT = "https://sdk.cloud.google.com"


def _already() -> str | None:
    exe = c.ensure_gcloud_on_path()
    if exe:
        c.ok(f"gcloud 이미 설치됨: {exe}")
    return exe


def _install_windows() -> bool:
    winget = c.find_exe("winget")
    if winget:
        c.info("winget 으로 Google Cloud SDK 설치 중... (몇 분 소요)")
        cp = c.run([winget, "install", "--id", "Google.CloudSDK", "-e",
                    "--accept-source-agreements", "--accept-package-agreements"])
        # winget returns non-zero when "already installed"; treat that as success.
        if cp.returncode == 0 or "already installed" in cp.stdout.lower() or \
           "이미 설치" in cp.stdout:
            return True
        c.report_failure("winget 설치", cp.stdout)
        c.warn("winget 설치 실패 — 공식 인스톨러로 재시도합니다.")
    else:
        c.warn("winget 을 찾지 못했습니다 — 공식 인스톨러를 내려받아 실행합니다.")
    # Fallback: download the interactive installer and launch it (GUI wizard).
    try:
        dst = Path(tempfile.gettempdir()) / "GoogleCloudSDKInstaller.exe"
        c.info(f"인스톨러 다운로드: {WINDOWS_INSTALLER_URL}")
        urllib.request.urlretrieve(WINDOWS_INSTALLER_URL, dst)
        c.info("설치 마법사를 실행합니다. 화면 안내를 따라 설치를 완료하세요.")
        subprocess.Popen([str(dst)])
        c.warn("설치 완료 후, 새 터미널을 열고 bootstrap.py 를 다시 실행하세요.")
        return False  # can't continue in this session for the GUI path
    except Exception as e:  # noqa: BLE001
        c.report_failure("인스톨러 다운로드/실행", str(e))
        c.info("수동 설치: https://cloud.google.com/sdk/docs/install")
        return False


def _install_macos() -> bool:
    brew = c.find_exe("brew")
    if brew:
        c.info("Homebrew 로 google-cloud-sdk 설치 중...")
        cp = c.run([brew, "install", "--cask", "google-cloud-sdk"])
        if cp.returncode == 0 or "already installed" in cp.stdout.lower():
            return True
        c.report_failure("brew 설치", cp.stdout)
        c.warn("brew 실패 — 공식 스크립트로 재시도합니다.")
    return _install_unix_script()


def _install_unix_script() -> bool:
    c.info("공식 설치 스크립트 실행 (curl | bash)...")
    # Interactive installer; inherit the terminal so prompts work.
    proc = subprocess.run(
        f"curl -fsSL {UNIX_INSTALL_SCRIPT} | bash", shell=True)
    if proc.returncode != 0:
        c.err("설치 스크립트가 실패했습니다.")
        c.info("수동 설치: https://cloud.google.com/sdk/docs/install")
        return False
    return True


def _post_auth() -> None:
    exe = c.ensure_gcloud_on_path()
    if not exe:
        c.warn("설치는 됐지만 이 세션 PATH 에서 gcloud 를 찾지 못했습니다.")
        c.info("새 터미널을 열고 bootstrap.py 를 다시 실행하면 이어집니다.")
        return
    c.ok(f"gcloud 사용 가능: {exe}")
    # Only prompt-login if there's no active account yet.
    acct = c.run([exe, "auth", "list", "--filter=status:ACTIVE",
                  "--format=value(account)"], echo=False)
    if not acct.stdout.strip():
        c.info("브라우저에서 구글 계정으로 로그인하세요 (gcloud auth login).")
        subprocess.run([exe, "auth", "login"])
    else:
        c.ok(f"이미 로그인됨: {acct.stdout.strip().splitlines()[0]}")
    # Set project from .env if available and not already set.
    project = c.env_value("GOOGLE_PROJECT_ID")
    if project and not project.startswith("your"):
        cur = c.run([exe, "config", "get-value", "project"], echo=False).stdout.strip()
        if cur != project:
            c.run([exe, "config", "set", "project", project])
            c.ok(f"프로젝트 설정: {project}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-auth", action="store_true", help="설치만 하고 로그인/프로젝트 설정은 건너뜀")
    args = ap.parse_args(argv)

    c.step("gcloud CLI 설치")
    if _already():
        if not args.no_auth:
            _post_auth()
        return 0

    system = platform.system()
    if system == "Windows":
        installed = _install_windows()
    elif system == "Darwin":
        installed = _install_macos()
    elif system == "Linux":
        installed = _install_unix_script()
    else:
        c.err(f"지원하지 않는 OS: {system}. 수동 설치: https://cloud.google.com/sdk/docs/install")
        return 1

    if not installed:
        return 1
    c.ok("gcloud 설치 완료.")
    if not args.no_auth:
        _post_auth()
    # Confirm reachability for the caller.
    return 0 if c.ensure_gcloud_on_path() else 2


if __name__ == "__main__":
    raise SystemExit(main())
