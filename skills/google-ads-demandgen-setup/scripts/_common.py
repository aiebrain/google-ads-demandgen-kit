#!/usr/bin/env python
"""Shared helpers for the google-ads-demandgen-setup scripts.

Central place for: locating executables (with Windows .cmd fallback), running
subprocesses while capturing combined output, config-dir paths, colored status
printing, and — most importantly — turning a raw error blob into a concrete,
actionable remediation via diagnose(). Every script funnels failures through
diagnose() so a stuck user gets the fix, not a stack trace.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Windows legacy consoles default to cp949/cp1252; Korean status text would either
# garble or raise UnicodeEncodeError. Force UTF-8 so output is consistent everywhere.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

# --- paths ------------------------------------------------------------------
# The reusable server/deploy/oauth assets live in the sibling skill. This file
# is <skills>/google-ads-demandgen-setup/scripts/_common.py, so parents[2] is
# the skills dir.
SKILLS_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = SKILLS_DIR / "google-ads-direct-mcp" / "assets"
CONFIG_HOME = Path(os.environ.get("GOOGLE_ADS_MCP_HOME", str(Path.home() / ".google-ads-mcp")))
ENV_FILE = CONFIG_HOME / ".env"
OAUTH_CLIENT_FILE = CONFIG_HOME / "google_ads_oauth_client.json"
ADC_FILE = CONFIG_HOME / "google_ads_adc.json"
BEARER_FILE = CONFIG_HOME / "mcp_bearer_token.txt"
CONNECTION_FILE = CONFIG_HOME / "connection.json"
LAST_DEPLOY_FILE = ASSETS_DIR / "last_deploy.json"

# --- pretty printing --------------------------------------------------------
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def step(msg: str) -> None:
    print(_c("1;36", f"\n==> {msg}"), flush=True)


def ok(msg: str) -> None:
    print(_c("32", f"  [OK] {msg}"), flush=True)


def warn(msg: str) -> None:
    print(_c("33", f"  [!]  {msg}"), flush=True)


def err(msg: str) -> None:
    print(_c("31", f"  [X]  {msg}"), flush=True)


def info(msg: str) -> None:
    print(f"       {msg}", flush=True)


# --- executables ------------------------------------------------------------
def find_exe(name: str) -> str | None:
    """Locate a CLI, tolerating Windows .cmd/.exe wrappers (gcloud, claude, npm)."""
    for candidate in (name, f"{name}.cmd", f"{name}.exe", f"{name}.bat"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def gcloud_bin_dir() -> Path | None:
    """Find the gcloud bin directory even when it isn't on PATH yet (e.g. right
    after installing in the same session). Returns the dir, or None."""
    from_path = find_exe("gcloud")
    if from_path:
        return Path(from_path).parent
    home = Path.home()
    candidates = [
        # Windows (winget / installer defaults)
        home / "AppData" / "Local" / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin",
        Path(r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin"),
        Path(r"C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin"),
        # macOS / Linux
        home / "google-cloud-sdk" / "bin",
        Path("/usr/lib/google-cloud-sdk/bin"),
        Path("/opt/google-cloud-sdk/bin"),
        Path("/snap/google-cloud-sdk/current/bin"),
    ]
    for d in candidates:
        try:
            if d and d.is_dir() and (list(d.glob("gcloud*"))):
                return d
        except OSError:
            continue
    return None


def ensure_gcloud_on_path() -> str | None:
    """Make gcloud reachable in this process (and children we spawn) by prepending
    its bin dir to PATH if needed. Returns the gcloud executable path or None."""
    exe = find_exe("gcloud")
    if exe:
        return exe
    bindir = gcloud_bin_dir()
    if bindir:
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        return find_exe("gcloud")
    return None


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
        unset: list[str] | None = None, check: bool = False, echo: bool = True
        ) -> subprocess.CompletedProcess:
    """Run a command, capturing stdout+stderr together. Never raises on non-zero
    unless check=True; callers usually inspect .returncode and .stdout and hand
    the text to diagnose() themselves. `unset` removes vars from the child env
    (e.g. CLAUDECODE so nested `claude` calls are allowed)."""
    if echo:
        shown = " ".join(str(c) for c in cmd)
        print(_c("2", f"       $ {shown}"), flush=True)
    full_env = {**os.environ, **(env or {})}
    for key in (unset or []):
        full_env.pop(key, None)
    cp = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=full_env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if check and cp.returncode != 0:
        print(cp.stdout)
        raise subprocess.CalledProcessError(cp.returncode, cmd, output=cp.stdout)
    return cp


# --- .env access ------------------------------------------------------------
def env_value(key: str, path: Path = ENV_FILE) -> str:
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


# --- error diagnosis --------------------------------------------------------
# Each entry: (compiled regex over the error text, one-line cause, fix steps).
# Ordered most-specific first. diagnose() returns the first match.
_SIGNATURES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"developer token is not valid|DEVELOPER_TOKEN_INVALID|not.*allowlisted", re.I),
     "개발자 토큰 값 자체가 유효하지 않습니다(오타/잘못된 값/잘못된 계정).",
     "관리자(MCC) 계정 → 도구 및 설정 → API 센터(https://ads.google.com/aw/apicenter)에서 "
     "개발자 토큰을 다시 복사해 .env 의 GOOGLE_ADS_DEVELOPER_TOKEN 에 정확히 넣으세요. "
     "토큰을 발급한 MCC 와 GOOGLE_ADS_LOGIN_CUSTOMER_ID 가 같은 계정이어야 합니다."),
    (re.compile(r"cannot be launched inside another Claude Code|Nested sessions share", re.I),
     "Claude Code 세션 안에서 claude 명령을 실행해 중첩이 차단됐습니다.",
     "CLAUDECODE 환경변수를 빼고 실행하세요. 스크립트는 자동 처리하며, 수동으로는 "
     "`env -u CLAUDECODE claude mcp add ...` (PowerShell: `$env:CLAUDECODE=''; claude mcp add ...`)."),
    (re.compile(r"DEVELOPER_TOKEN_NOT_APPROVED", re.I),
     "개발자 토큰이 아직 Test 등급이라 실계정 호출이 막혔습니다.",
     "Google Ads 관리자(MCC) → 도구 및 설정 → API 센터에서 'Basic 액세스'를 신청하세요. "
     "승인 전에는 테스트 계정에서만 동작합니다. 신청/상태 확인: https://ads.google.com/aw/apicenter"),
    (re.compile(r"DEVELOPER_TOKEN_PROHIBITED|developer token .*prohibited", re.I),
     "이 개발자 토큰으로는 해당 계정을 호출할 수 없습니다.",
     "토큰을 발급한 MCC 아래에 대상 광고계정이 연결(link)돼 있는지, login_customer_id가 그 MCC ID인지 확인하세요."),
    (re.compile(r"invalid_grant|Token has been expired or revoked", re.I),
     "OAuth 리프레시 토큰이 만료/취소됐습니다.",
     "oauth_setup을 다시 실행해 새 google_ads_adc.json을 만드세요: bootstrap.py --reauth. "
     "그 뒤 재배포하면 새 토큰이 반영됩니다."),
    (re.compile(r"USER_PERMISSION_DENIED|user (doesn't|does not) have permission", re.I),
     "인증한 구글 계정이 대상 광고계정에 대한 권한이 없습니다.",
     "Google Ads에서 그 계정(또는 MCC)에 로그인한 구글 계정이 접근 권한을 갖는지 확인하세요. "
     "OAuth 동의 때 쓴 계정과 광고계정 접근 계정이 같아야 합니다."),
    (re.compile(r"CUSTOMER_NOT_FOUND|customer .*not found|INVALID_CUSTOMER_ID", re.I),
     "고객 ID(customer_id) 또는 login_customer_id가 잘못됐습니다.",
     ".env의 GOOGLE_ADS_LOGIN_CUSTOMER_ID(=MCC, 하이픈 없이 10자리)와 실제 광고가 도는 "
     "GOOGLE_ADS_CUSTOMER_ID를 확인하세요. 관리자 계정 ID를 customer_id로 넣으면 안 됩니다."),
    (re.compile(r"billing.*(not|disabled|required)|FAILED_PRECONDITION.*billing|Billing account", re.I),
     "GCP 프로젝트에 결제 계정이 연결되지 않았습니다.",
     "https://console.cloud.google.com/billing 에서 이 프로젝트에 결제 계정을 연결하세요. "
     "Cloud Run 무료 한도는 넉넉하지만 결제 연결 자체는 필수입니다."),
    (re.compile(r"secretmanager.*(permission|denied)|Permission.*secret|does not have.*secretAccessor", re.I),
     "Cloud Run 런타임 서비스계정이 시크릿을 읽을 권한이 없습니다.",
     "deploy_cloud_run.py가 자동으로 secretAccessor를 부여하지만 실패했다면 수동으로: "
     "gcloud secrets add-iam-policy-binding <secret> --member serviceAccount:<PROJNUM>-compute@developer.gserviceaccount.com "
     "--role roles/secretmanager.secretAccessor"),
    (re.compile(r"SERVICE_DISABLED|has not been used in project|API .*is not enabled|Enable it by visiting", re.I),
     "필요한 GCP API가 아직 활성화되지 않았습니다.",
     "gcloud services enable run.googleapis.com cloudbuild.googleapis.com "
     "secretmanager.googleapis.com artifactregistry.googleapis.com (bootstrap가 자동 시도합니다)."),
    (re.compile(r"gcloud auth login|Reauthentication (required|failed)|credentials.*not.*found|Please run:\s*\$ gcloud auth", re.I),
     "gcloud 로그인 세션이 없거나 만료됐습니다.",
     "gcloud auth login 을 실행해 다시 로그인하세요. 그 뒤 gcloud config set project <PROJECT_ID>."),
    (re.compile(r"The caller does not have permission|PERMISSION_DENIED.*(run|cloudbuild|iam)", re.I),
     "gcloud 계정에 배포/빌드 권한(역할)이 부족합니다.",
     "프로젝트에서 본인 계정에 Owner 또는 (Cloud Run Admin + Cloud Build Editor + "
     "Service Account User + Secret Manager Admin) 역할을 부여하세요."),
    (re.compile(r"No such file or directory.*requirements|ModuleNotFoundError|No module named", re.I),
     "파이썬 의존성이 설치되지 않았습니다.",
     f"pip install -r \"{ASSETS_DIR / 'requirements.txt'}\" (bootstrap가 자동 시도합니다)."),
    (re.compile(r"quota|RESOURCE_EXHAUSTED|RATE_EXCEEDED", re.I),
     "API 쿼터/속도 제한에 걸렸습니다.",
     "잠시 후 다시 시도하세요. 반복되면 Google Ads API 콘솔에서 쿼터 상향을 신청하세요."),
]


def diagnose(text: str) -> tuple[str, str] | None:
    """Return (cause, fix) for the first matching known error signature, else None."""
    if not text:
        return None
    for pattern, cause, fix in _SIGNATURES:
        if pattern.search(text):
            return cause, fix
    return None


def report_failure(context: str, output: str) -> None:
    """Print a failed step's output plus a diagnosis (if we recognize the error)."""
    err(f"{context} 실패")
    tail = "\n".join(output.strip().splitlines()[-25:]) if output else ""
    if tail:
        print(_c("2", tail))
    hit = diagnose(output)
    if hit:
        cause, fix = hit
        print(_c("1;33", f"  진단: {cause}"))
        print(_c("36", f"  해결: {fix}"))
    else:
        print(_c("33", "  자동 진단에 매칭되는 알려진 오류가 아닙니다. "
                        "references/troubleshooting.md 를 참고하세요."))


def open_url(url: str) -> None:
    """Open a URL in the user's default browser (best-effort, cross-platform)."""
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - opening a browser is a convenience, never fatal
        pass


def set_env_value(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Create/update a KEY=VALUE line in the .env (creates the file/dir if needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped \
                and stripped.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(raw)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def load_connection() -> dict:
    """Read saved connection info (mcp_url + bearer token). Checks the synced
    config dir first, then falls back to the deploy artifact in assets."""
    if CONNECTION_FILE.exists():
        return json.loads(CONNECTION_FILE.read_text(encoding="utf-8"))
    if LAST_DEPLOY_FILE.exists():
        data = json.loads(LAST_DEPLOY_FILE.read_text(encoding="utf-8"))
        if BEARER_FILE.exists():
            data["bearer_token"] = BEARER_FILE.read_text(encoding="utf-8").strip()
        return data
    return {}
