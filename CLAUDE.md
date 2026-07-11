# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This directory holds a single distributable artifact: `google-ads-direct-mcp.skill` — a packaged Claude Code skill (a ZIP archive with a `.skill` extension). There is no unpacked source tree here; to change anything you must extract the archive, edit, and repack.

The skill is a tutorial + bundled code for deploying a **write-capable Google Ads MCP server** to Cloud Run (most open-source Google Ads MCP servers are read-only). The deployed server is registered in Claude Code as `google_ads_direct` and exposes ~25 tools (GAQL search, budgets, search campaigns, ad groups, keywords, RSAs, geo/language targeting, Demand Gen campaigns/ads/assets).

## Working with the archive

```powershell
# Inspect / extract (it is a plain ZIP)
Expand-Archive -Path google-ads-direct-mcp.skill -DestinationPath .\work

# Repack after editing — the top-level folder name inside the ZIP must stay
# `google-ads-direct-mcp/` (matching the skill name in SKILL.md frontmatter)
Compress-Archive -Path .\work\google-ads-direct-mcp -DestinationPath google-ads-direct-mcp.skill -Force
```

## Archive contents

```
google-ads-direct-mcp/
├── SKILL.md                          # Skill definition + Korean step-by-step tutorial (Step 0–9)
├── assets/
│   ├── google_ads_mcp_server.py      # The MCP server itself (~1150 lines, all tools)
│   ├── oauth_setup.py                # One-time OAuth flow → google_ads_adc.json (refresh token)
│   ├── deploy_cloud_run.py           # One-shot deploy: enable APIs → secrets → IAM → gcloud run deploy
│   ├── Dockerfile / requirements.txt / .env.example / .gcloudignore
└── references/troubleshooting.md     # Symptom → cause → fix for auth/deploy/connection errors
```

## Server architecture (google_ads_mcp_server.py)

- **FastMCP stateless streamable-HTTP** app served at `/mcp`; `/` and `/healthz` are open, everything else is guarded by `BearerAuthMiddleware` comparing against the `MCP_BEARER_TOKEN` env var. Requires `mcp>=1.9.0` (do not loosen — `streamable_http_app`, `stateless_http`, `TransportSecuritySettings` need it).
- **Config is env-only** (no hardcoded secrets): `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_ADC_JSON` (OAuth authorized-user JSON as a string, injected from Secret Manager in prod), `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC), `GOOGLE_ADS_CUSTOMER_ID` (default account). Local dev falls back to `~/.google-ads-mcp/` (`.env` + `google_ads_adc.json`), overridable via `GOOGLE_ADS_MCP_HOME`.
- **Safety conventions — every mutation tool must follow these** (see `references/troubleshooting.md` "커스텀 툴 추가할 때"):
  - New campaigns/ad groups/ads are created **PAUSED** by default.
  - Setting status to ENABLED requires `confirm_enable='ENABLE'`; deletion requires `confirm_delete='DELETE'`.
  - All tool returns go through `_json(...)`, which redacts tokens/secrets from output.
  - Customer IDs are normalized with `(customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")`.
- **Google Ads API v17+ gotcha**: campaign creation must set `campaign.contains_eu_political_advertising = ...DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING` or the API rejects with "The required field was not present". Existing campaign tools already do this; new campaign tools must too.
- **Demand Gen constraints**: bidding must be conversion/click-based (no manual CPC; use `MAXIMIZE_CLICKS` without conversion tracking); videos must already exist on YouTube (referenced by video ID); images are passed as public URLs the server downloads (≤10 MB) — never base64 through the model. `call_to_action_text` takes display text (`"Learn more"`), not enum tokens.

## Commands (run inside extracted assets/)

```bash
pip install -r requirements.txt
python oauth_setup.py                          # one-time OAuth → google_ads_adc.json
python google_ads_mcp_server.py --self-test    # verify credentials before deploying (lists accounts)
python deploy_cloud_run.py                     # full deploy; REGION=us-central1 to override (default asia-northeast3)
```

Deploy secrets live in Secret Manager as `google-ads-developer-token`, `google-ads-adc-json`, `google-ads-mcp-bearer-token`; the deploy script grants the Cloud Run compute SA `secretmanager.secretAccessor` on each (missing this is the #1 first-boot crash). Deploy output is recorded in `last_deploy.json`.

There is no test suite or linter; verification is `--self-test` locally, `curl <url>/healthz` after deploy, then read-only tool calls from Claude Code before any write.

## Never commit or bundle

`.env`, `google_ads_adc.json`, `mcp_bearer_token.txt`, and the downloaded OAuth client JSON are secrets (`.gcloudignore` already excludes them from the Cloud Run image). The bearer token is the only lock on a public URL — it equals spend authority on the linked ad accounts.

## Related context

- A newer/installed copy of this skill may exist at `~/.claude/skills/`; the related `google-ads-demandgen-setup` skill reuses these server assets as an automation layer. If you change server code here, consider whether the installed skill copy should be updated too.
- Skill docs are written in Korean; keep that language when editing SKILL.md or troubleshooting.md.
