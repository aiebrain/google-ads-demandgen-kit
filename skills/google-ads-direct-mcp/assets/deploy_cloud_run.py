#!/usr/bin/env python
"""Deploy the Google Ads MCP server to Cloud Run in one shot.

Does everything that trips people up on a first deploy:
  1. Enables the required APIs (run, cloudbuild, secretmanager, artifactregistry).
  2. Creates/updates three secrets in Secret Manager (dev token, ADC json, bearer token).
  3. Deploys from source (Cloud Build builds the Dockerfile) with the secrets bound in.
  4. Prints the MCP URL to register in Claude Code.

Prereqs: gcloud CLI installed and `gcloud auth login` done. Config dir holds .env +
google_ads_adc.json (see oauth_setup.py). The bearer token is read from a file next to
this script (mcp_bearer_token.txt) or the MCP_BEARER_TOKEN env var.

Env overrides: PROJECT_ID, REGION (default asia-northeast3), SERVICE (default google-ads-mcp),
GOOGLE_ADS_MCP_HOME (default ~/.google-ads-mcp).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_HOME = Path(os.environ.get("GOOGLE_ADS_MCP_HOME", str(Path.home() / ".google-ads-mcp")))
ENV_FILE = CONFIG_HOME / ".env"
ADC_FILE = CONFIG_HOME / "google_ads_adc.json"
TOKEN_FILE = ROOT / "mcp_bearer_token.txt"
REGION = os.environ.get("REGION", "asia-northeast3")
SERVICE = os.environ.get("SERVICE", "google-ads-mcp")
GCLOUD = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud"


def gcloud(*args: str) -> list[str]:
    return [GCLOUD, *args]


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("$", "gcloud", *(cmd[1:3] if len(cmd) > 2 else cmd[1:]), "...", flush=True)
    cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and cp.returncode != 0:
        if cp.stdout:
            print(cp.stdout)
        raise subprocess.CalledProcessError(cp.returncode, cmd, output=cp.stdout)
    return cp


def env_value(key: str) -> str:
    if not ENV_FILE.exists():
        return ""
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def ensure_secret(name: str, value: str) -> None:
    if not value:
        raise SystemExit(f"missing value for secret {name}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
        f.write(value)
        tmp = f.name
    try:
        exists = run(gcloud("secrets", "describe", name), check=False).returncode == 0
        if exists:
            run(gcloud("secrets", "versions", "add", name, f"--data-file={tmp}"))
        else:
            run(gcloud("secrets", "create", name, f"--data-file={tmp}", "--replication-policy=automatic"))
    finally:
        try:
            Path(tmp).unlink()
        except OSError:
            pass


def main() -> int:
    if not (shutil.which(GCLOUD) or Path(GCLOUD).exists()):
        print("ERROR: gcloud CLI is not installed or not on PATH.")
        return 10

    project_id = os.environ.get("PROJECT_ID") or env_value("GOOGLE_PROJECT_ID")
    if not project_id:
        project_id = run(gcloud("config", "get-value", "project"), check=False).stdout.strip()
    if not project_id or project_id == "(unset)":
        print("ERROR: PROJECT_ID not set and gcloud has no default project.")
        return 11
    if not ADC_FILE.exists():
        print(f"ERROR: missing ADC file: {ADC_FILE}  (run oauth_setup.py first)")
        return 12

    mcp_token = os.environ.get("MCP_BEARER_TOKEN", "").strip()
    if not mcp_token and TOKEN_FILE.exists():
        mcp_token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not mcp_token:
        print(f"ERROR: no bearer token. Put one in {TOKEN_FILE} or set MCP_BEARER_TOKEN.")
        return 13

    dev_token = env_value("GOOGLE_ADS_DEVELOPER_TOKEN")
    adc_json = ADC_FILE.read_text(encoding="utf-8")
    json.loads(adc_json)  # validate

    print(f"Deploying service={SERVICE} project={project_id} region={REGION}")
    run(gcloud("config", "set", "project", project_id))
    run(gcloud("services", "enable", "run.googleapis.com", "cloudbuild.googleapis.com",
               "secretmanager.googleapis.com", "artifactregistry.googleapis.com"))

    secret_names = ("google-ads-developer-token", "google-ads-adc-json", "google-ads-mcp-bearer-token")
    ensure_secret(secret_names[0], dev_token)
    ensure_secret(secret_names[1], adc_json)
    ensure_secret(secret_names[2], mcp_token)

    # Grant the Cloud Run runtime service account access to each secret. Without this the
    # container often crashes on first boot with a Secret Manager permission error. The
    # default runtime SA is the compute SA; if you set a custom one, adjust `runtime_sa`.
    proj_num = run(gcloud("projects", "describe", project_id, "--format=value(projectNumber)")).stdout.strip()
    runtime_sa = os.environ.get("RUNTIME_SERVICE_ACCOUNT", f"{proj_num}-compute@developer.gserviceaccount.com")
    for secret in secret_names:
        run(gcloud("secrets", "add-iam-policy-binding", secret,
                   "--member", f"serviceAccount:{runtime_sa}",
                   "--role", "roles/secretmanager.secretAccessor"), check=False)

    # Pass account ids as env vars so tools that default to them work in the remote server.
    # Only include values that are actually set, so empty defaults don't override anything.
    env_pairs = [f"GOOGLE_PROJECT_ID={project_id}"]
    login_cid = env_value("GOOGLE_ADS_LOGIN_CUSTOMER_ID").replace("-", "").strip()
    default_cid = env_value("GOOGLE_ADS_CUSTOMER_ID").replace("-", "").strip()
    if login_cid:
        env_pairs.append(f"GOOGLE_ADS_LOGIN_CUSTOMER_ID={login_cid}")
    if default_cid:
        env_pairs.append(f"GOOGLE_ADS_CUSTOMER_ID={default_cid}")

    run(gcloud(
        "run", "deploy", SERVICE,
        "--source", ".",
        "--region", REGION,
        "--allow-unauthenticated",
        "--set-secrets", "GOOGLE_ADS_DEVELOPER_TOKEN=google-ads-developer-token:latest,"
                          "GOOGLE_ADS_ADC_JSON=google-ads-adc-json:latest,"
                          "MCP_BEARER_TOKEN=google-ads-mcp-bearer-token:latest",
        "--set-env-vars", ",".join(env_pairs),
        "--memory", "512Mi", "--cpu", "1", "--timeout", "300", "--quiet",
    ))
    url = run(gcloud("run", "services", "describe", SERVICE, "--region", REGION,
                     "--format=value(status.url)")).stdout.strip()
    print("\n=== DEPLOY COMPLETE ===")
    print(f"SERVICE_URL = {url}")
    print(f"MCP_URL     = {url}/mcp")
    print("Register in Claude Code with (replace <YOUR_BEARER_TOKEN> with the value in mcp_bearer_token.txt):")
    print(f'  claude mcp add --transport http google_ads_direct {url}/mcp \\')
    print('    --header "Authorization: Bearer <YOUR_BEARER_TOKEN>"')
    (ROOT / "last_deploy.json").write_text(
        json.dumps({"service_url": url, "mcp_url": url + "/mcp", "region": REGION,
                    "service": SERVICE, "project_id": project_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
