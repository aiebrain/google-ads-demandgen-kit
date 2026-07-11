#!/usr/bin/env python
"""Remote HTTP MCP server that connects Claude Code directly to the Google Ads API.

Design goals:
- Read tools (GAQL search, campaign report, account listing) always available.
- Mutation tools (budget/campaign create/update/delete) enabled but SAFE by default:
  new campaigns are PAUSED, and enabling/deleting require explicit confirmation args.
- Secrets are read from environment variables (injected by Cloud Run Secret Manager in
  production, or from a local config dir when developing). Nothing is hardcoded.
- Tool output is redacted so tokens never leak back into the chat transcript.

Config (all via environment variables):
  GOOGLE_ADS_DEVELOPER_TOKEN   (required) - your approved Google Ads API developer token
  GOOGLE_ADS_ADC_JSON          - the OAuth "authorized_user" JSON as a single string
                                 (client_id/client_secret/refresh_token). Used in prod.
  GOOGLE_ADS_MCP_HOME          - local dir holding .env + google_ads_adc.json (dev only,
                                 default: ~/.google-ads-mcp)
  GOOGLE_ADS_LOGIN_CUSTOMER_ID - MCC/manager account id, digits only (optional)
  GOOGLE_ADS_CUSTOMER_ID       - default account id used when a tool call omits one
  MCP_BEARER_TOKEN             - shared secret required in the Authorization header
  PORT                         - HTTP port (Cloud Run injects this)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import proto
from google.api_core import protobuf_helpers
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.util import get_nested_attr
from google.oauth2.credentials import Credentials
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

CONFIG_HOME = Path(os.environ.get("GOOGLE_ADS_MCP_HOME", str(Path.home() / ".google-ads-mcp")))
ENV_PATH = CONFIG_HOME / ".env"
ADC_PATH = CONFIG_HOME / "google_ads_adc.json"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
# Optional default account so convenience tools work without repeating the id every call.
DEFAULT_CUSTOMER_ID = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "").strip()

mcp = FastMCP(
    "google_ads_direct",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    streamable_http_path="/mcp",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

SECRET_PATTERNS = [
    r"(?i)(developer[_-]?token\s*[:=]\s*)\S+",
    r"(?i)(client[_-]?secret\s*[:=]\s*)\S+",
    r"(?i)(refresh[_-]?token\s*[:=]\s*)\S+",
    r"(?i)(access[_-]?token\s*[:=]\s*)\S+",
    r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+",
]


def _load_env(path: Path = ENV_PATH) -> None:
    """Local dev convenience: read KEY=VALUE lines from a .env into the environment."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _redact_text(text: str) -> str:
    out = text
    for pat in SECRET_PATTERNS:
        out = re.sub(pat, lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", out)
    return out


def _json(data: Any) -> str:
    return _redact_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _build_client() -> GoogleAdsClient:
    _load_env()
    developer_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not developer_token:
        raise RuntimeError(f"GOOGLE_ADS_DEVELOPER_TOKEN is missing; set it in env or {ENV_PATH}")
    adc_json = os.environ.get("GOOGLE_ADS_ADC_JSON", "").strip()
    if adc_json:
        adc_info = json.loads(adc_json)
    elif ADC_PATH.exists():
        adc_info = json.loads(ADC_PATH.read_text(encoding="utf-8"))
    else:
        raise RuntimeError(f"OAuth credentials missing; set GOOGLE_ADS_ADC_JSON or create {ADC_PATH}")
    credentials = Credentials.from_authorized_user_info(adc_info, scopes=[ADS_SCOPE])
    kwargs: dict[str, Any] = {
        "credentials": credentials,
        "developer_token": developer_token,
        "use_proto_plus": True,
    }
    login_customer_id = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").replace("-", "").strip()
    if login_customer_id:
        kwargs["login_customer_id"] = login_customer_id
    return GoogleAdsClient(**kwargs)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Shared-secret guard for remote MCP HTTP access. Health/root paths stay open."""

    async def dispatch(self, request, call_next):
        if request.url.path in {"/", "/healthz"}:
            return await call_next(request)
        expected = os.environ.get("MCP_BEARER_TOKEN", "").strip()
        if expected:
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {expected}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app = mcp.streamable_http_app()
app.add_middleware(BearerAuthMiddleware)


async def healthz(_request):
    return JSONResponse({"ok": True, "server": "google_ads_direct"})


async def root(_request):
    return PlainTextResponse("google_ads_direct MCP server. Use /mcp with Authorization: Bearer <token>.")


app.add_route("/healthz", healthz, methods=["GET"])
app.add_route("/", root, methods=["GET"])


def _value(x: Any) -> Any:
    if isinstance(x, proto.Enum):
        return x.name
    if hasattr(x, "name") and hasattr(x, "value"):
        return x.name
    if isinstance(x, (list, tuple)):
        return [_value(i) for i in x]
    return x


def _google_ads_error(ex: GoogleAdsException) -> dict[str, Any]:
    return {
        "error": "GoogleAdsException",
        "request_id": ex.request_id,
        "messages": [err.message for err in ex.failure.errors],
    }


def _run_query(customer_id: str, query: str, fields: list[str], limit: int = 500) -> list[dict[str, Any]]:
    client = _build_client()
    service = client.get_service("GoogleAdsService")
    out: list[dict[str, Any]] = []
    for batch in service.search_stream(customer_id=customer_id.replace("-", ""), query=query):
        for row in batch.results:
            item: dict[str, Any] = {}
            for field in fields:
                try:
                    item[field] = _value(get_nested_attr(row, field))
                except Exception:
                    item[field] = None
            out.append(item)
            if len(out) >= max(1, min(int(limit), 5000)):
                return out
    return out


@mcp.tool()
def list_accessible_customers() -> str:
    """List Google Ads customer IDs accessible by the configured OAuth user."""
    try:
        client = _build_client()
        service = client.get_service("CustomerService")
        customer_ids = [rn.replace("customers/", "") for rn in service.list_accessible_customers().resource_names]
        return _json({"customers": customer_ids, "default_customer_id": DEFAULT_CUSTOMER_ID})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def search_google_ads(customer_id: str, query: str, fields_csv: str, limit: int = 500) -> str:
    """Run a read-only GAQL query and return selected fields as JSON.

    Args:
        customer_id: Google Ads customer ID without hyphens, e.g. 1234567890.
        query: Full GAQL SELECT query. Use finite date ranges for metrics.
        fields_csv: Comma-separated field paths from the SELECT clause to extract, e.g.
            "segments.date,campaign.name,metrics.clicks,metrics.cost_micros".
        limit: Maximum rows to return, capped at 5000.
    """
    try:
        lower = query.lower()
        forbidden = [" mutate", "remove ", "create ", "update ", "delete ", "insert "]
        if any(token in lower for token in forbidden):
            return _json({"error": "read_only", "message": "search_google_ads only allows read-only GAQL SELECT queries."})
        fields = [f.strip() for f in fields_csv.split(",") if f.strip()]
        if not fields:
            return _json({"error": "missing_fields_csv", "message": "Pass the selected fields as fields_csv."})
        rows = _run_query(customer_id or DEFAULT_CUSTOMER_ID, query, fields, limit)
        return _json({"customer_id": customer_id or DEFAULT_CUSTOMER_ID, "row_count": len(rows), "rows": rows})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def campaign_performance(customer_id: str = "", start_date: str = "", end_date: str = "", limit: int = 50) -> str:
    """Convenience report: campaign metrics by day for a finite date range (YYYY-MM-DD)."""
    if not start_date or not end_date:
        return _json({"error": "date_range_required", "message": "Pass start_date and end_date as YYYY-MM-DD."})
    fields = [
        "segments.date", "campaign.id", "campaign.name", "campaign.status",
        "campaign.advertising_channel_type", "metrics.impressions", "metrics.clicks",
        "metrics.cost_micros", "metrics.conversions", "metrics.conversions_value",
    ]
    query = f"""
        SELECT {','.join(fields)}
        FROM campaign
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY segments.date DESC, metrics.conversions DESC
    """
    try:
        cid = customer_id or DEFAULT_CUSTOMER_ID
        rows = _run_query(cid, query, fields, limit)
        return _json({"customer_id": cid, "start_date": start_date, "end_date": end_date, "row_count": len(rows), "rows": rows})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def customer_children(login_customer_id: str = "") -> str:
    """List child/client accounts visible under a manager (MCC) account."""
    cid = (login_customer_id or os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or DEFAULT_CUSTOMER_ID).replace("-", "").strip()
    if not cid:
        return _json({"error": "login_customer_id_required", "message": "Pass a manager account id or set GOOGLE_ADS_LOGIN_CUSTOMER_ID."})
    fields = [
        "customer_client.client_customer", "customer_client.id", "customer_client.descriptive_name",
        "customer_client.manager", "customer_client.level", "customer_client.status",
        "customer_client.currency_code", "customer_client.time_zone",
    ]
    query = f"SELECT {','.join(fields)} FROM customer_client WHERE customer_client.level <= 1"
    try:
        rows = _run_query(cid, query, fields, 500)
        return _json({"login_customer_id": cid, "row_count": len(rows), "rows": rows})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


def _enum_value(client: GoogleAdsClient, enum_name: str, value_name: str) -> Any:
    enum = getattr(client.enums, enum_name)
    normalized = (value_name or "").strip().upper()
    if not normalized:
        raise ValueError(f"{enum_name} value is required")
    return getattr(enum, normalized)


def _campaign_path(client: GoogleAdsClient, customer_id: str, campaign_id: str) -> str:
    return client.get_service("CampaignService").campaign_path(customer_id.replace("-", ""), str(campaign_id).replace("-", ""))


@mcp.tool()
def create_campaign_budget(customer_id: str = "", name: str = "", amount_micros: int = 0, delivery_method: str = "STANDARD") -> str:
    """Create a Google Ads campaign budget and return its resource name.

    Mutates the account but does not launch a campaign. amount_micros is in account
    currency micros, e.g. 50000000 = 50 currency units/day.
    """
    if not name.strip():
        return _json({"error": "name_required", "message": "Budget name is required."})
    if int(amount_micros) <= 0:
        return _json({"error": "positive_amount_required", "message": "amount_micros must be positive."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        budget_service = client.get_service("CampaignBudgetService")
        operation = client.get_type("CampaignBudgetOperation")
        budget = operation.create
        budget.name = name.strip()
        budget.amount_micros = int(amount_micros)
        budget.delivery_method = _enum_value(client, "BudgetDeliveryMethodEnum", delivery_method)
        budget.explicitly_shared = False
        response = budget_service.mutate_campaign_budgets(customer_id=cid, operations=[operation])
        return _json({
            "customer_id": cid,
            "created_budget_resource_name": response.results[0].resource_name,
            "safety_note": "Budget created. No campaign was enabled by this tool.",
        })
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def update_campaign_budget(customer_id: str = "", budget_resource_name: str = "", name: str = "", amount_micros: int = 0, delivery_method: str = "") -> str:
    """Update a campaign budget's name, amount_micros, or delivery_method."""
    if not budget_resource_name.strip():
        return _json({"error": "budget_resource_name_required", "message": "budget_resource_name is required."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        budget_service = client.get_service("CampaignBudgetService")
        operation = client.get_type("CampaignBudgetOperation")
        budget = operation.update
        budget.resource_name = budget_resource_name.strip()
        changed: list[str] = []
        if name.strip():
            budget.name = name.strip(); changed.append("name")
        if int(amount_micros or 0) > 0:
            budget.amount_micros = int(amount_micros); changed.append("amount_micros")
        if delivery_method.strip():
            budget.delivery_method = _enum_value(client, "BudgetDeliveryMethodEnum", delivery_method); changed.append("delivery_method")
        if not changed:
            return _json({"error": "no_updates", "message": "Pass name, positive amount_micros, or delivery_method."})
        operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, budget._pb))
        response = budget_service.mutate_campaign_budgets(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "updated_budget_resource_name": response.results[0].resource_name, "changed_fields": changed})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def create_search_campaign(customer_id: str = "", name: str = "", budget_resource_name: str = "", status: str = "PAUSED",
                           start_date: str = "", end_date: str = "", enhanced_cpc_enabled: bool = True, confirm_enable: str = "") -> str:
    """Create a SEARCH campaign. Defaults to PAUSED for safety.

    To create an ENABLED campaign, pass status='ENABLED' AND confirm_enable='ENABLE'.
    Otherwise status is forced to PAUSED. Dates are optional YYYYMMDD strings.
    """
    if not name.strip():
        return _json({"error": "name_required", "message": "Campaign name is required."})
    if not budget_resource_name.strip():
        return _json({"error": "budget_required", "message": "budget_resource_name is required. Create a budget first."})
    normalized_status = (status or "PAUSED").strip().upper()
    if normalized_status == "ENABLED" and confirm_enable != "ENABLE":
        normalized_status = "PAUSED"
    if normalized_status not in {"PAUSED", "ENABLED"}:
        normalized_status = "PAUSED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        campaign_service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        campaign = operation.create
        campaign.name = name.strip()
        campaign.status = _enum_value(client, "CampaignStatusEnum", normalized_status)
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
        # Required since Google Ads API v17+: declare EU political advertising status.
        campaign.contains_eu_political_advertising = client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        campaign.manual_cpc.enhanced_cpc_enabled = bool(enhanced_cpc_enabled)
        campaign.campaign_budget = budget_resource_name.strip()
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = True
        campaign.network_settings.target_content_network = False
        campaign.network_settings.target_partner_search_network = False
        if start_date.strip():
            campaign.start_date = start_date.strip()
        if end_date.strip():
            campaign.end_date = end_date.strip()
        response = campaign_service.mutate_campaigns(customer_id=cid, operations=[operation])
        return _json({
            "customer_id": cid,
            "created_campaign_resource_name": response.results[0].resource_name,
            "created_status": normalized_status,
            "safety_note": "Default is PAUSED. ENABLED only when confirm_enable='ENABLE'.",
        })
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def update_campaign(customer_id: str = "", campaign_id: str = "", name: str = "", status: str = "",
                    budget_resource_name: str = "", start_date: str = "", end_date: str = "", confirm_enable: str = "") -> str:
    """Update safe campaign fields: name, status, budget, start_date, end_date.

    Setting status=ENABLED requires confirm_enable='ENABLE'. Use delete_campaign to remove.
    """
    if not campaign_id.strip():
        return _json({"error": "campaign_id_required", "message": "campaign_id is required."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        campaign_service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = _campaign_path(client, cid, campaign_id)
        changed: list[str] = []
        if name.strip():
            campaign.name = name.strip(); changed.append("name")
        if status.strip():
            normalized_status = status.strip().upper()
            if normalized_status == "REMOVED":
                return _json({"error": "use_delete_campaign", "message": "Use delete_campaign with confirm_delete='DELETE'."})
            if normalized_status == "ENABLED" and confirm_enable != "ENABLE":
                return _json({"error": "enable_confirmation_required", "message": "Pass confirm_enable='ENABLE' to enable."})
            if normalized_status not in {"PAUSED", "ENABLED"}:
                return _json({"error": "unsupported_status", "message": "Supported statuses: PAUSED, ENABLED."})
            campaign.status = _enum_value(client, "CampaignStatusEnum", normalized_status); changed.append("status")
        if budget_resource_name.strip():
            campaign.campaign_budget = budget_resource_name.strip(); changed.append("campaign_budget")
        if start_date.strip():
            campaign.start_date = start_date.strip(); changed.append("start_date")
        if end_date.strip():
            campaign.end_date = end_date.strip(); changed.append("end_date")
        if not changed:
            return _json({"error": "no_updates", "message": "Pass at least one field to update."})
        operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, campaign._pb))
        response = campaign_service.mutate_campaigns(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "updated_campaign_resource_name": response.results[0].resource_name, "changed_fields": changed})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def delete_campaign(customer_id: str = "", campaign_id: str = "", confirm_delete: str = "") -> str:
    """Remove a campaign. Requires confirm_delete='DELETE'. Prefer pausing unless removal was requested."""
    if confirm_delete != "DELETE":
        return _json({"error": "delete_confirmation_required", "message": "Pass confirm_delete='DELETE' to remove a campaign."})
    if not campaign_id.strip():
        return _json({"error": "campaign_id_required", "message": "campaign_id is required."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        campaign_service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        operation.remove = _campaign_path(client, cid, campaign_id)
        response = campaign_service.mutate_campaigns(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "removed_campaign_resource_name": response.results[0].resource_name})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


# ---------------------------------------------------------------------------
# Ad group / keyword / ad / targeting tools — everything needed to build a
# working Search campaign under the shell created above. Same safety posture:
# new ad groups and ads default to PAUSED, mutations are batched where natural,
# and all output goes through _json() so nothing sensitive leaks back.
# ---------------------------------------------------------------------------


def _ad_group_path(client: GoogleAdsClient, customer_id: str, ad_group_id: str) -> str:
    return client.get_service("AdGroupService").ad_group_path(customer_id.replace("-", ""), str(ad_group_id).replace("-", ""))


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@mcp.tool()
def create_ad_group(customer_id: str = "", campaign_id: str = "", name: str = "",
                    cpc_bid_micros: int = 0, status: str = "PAUSED") -> str:
    """Create an ad group under a campaign. Defaults to PAUSED.

    cpc_bid_micros is the default max CPC in account currency micros (optional, 0 = unset).
    """
    if not campaign_id.strip():
        return _json({"error": "campaign_id_required", "message": "campaign_id is required."})
    if not name.strip():
        return _json({"error": "name_required", "message": "Ad group name is required."})
    normalized_status = (status or "PAUSED").strip().upper()
    if normalized_status not in {"PAUSED", "ENABLED"}:
        normalized_status = "PAUSED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AdGroupService")
        operation = client.get_type("AdGroupOperation")
        ag = operation.create
        ag.name = name.strip()
        ag.campaign = _campaign_path(client, cid, campaign_id)
        ag.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        ag.status = _enum_value(client, "AdGroupStatusEnum", normalized_status)
        if int(cpc_bid_micros or 0) > 0:
            ag.cpc_bid_micros = int(cpc_bid_micros)
        response = service.mutate_ad_groups(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "created_ad_group_resource_name": response.results[0].resource_name, "created_status": normalized_status})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def update_ad_group(customer_id: str = "", ad_group_id: str = "", name: str = "",
                    status: str = "", cpc_bid_micros: int = 0, confirm_enable: str = "") -> str:
    """Update an ad group's name, status, or default CPC. ENABLED needs confirm_enable='ENABLE'."""
    if not ad_group_id.strip():
        return _json({"error": "ad_group_id_required", "message": "ad_group_id is required."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AdGroupService")
        operation = client.get_type("AdGroupOperation")
        ag = operation.update
        ag.resource_name = _ad_group_path(client, cid, ad_group_id)
        changed: list[str] = []
        if name.strip():
            ag.name = name.strip(); changed.append("name")
        if status.strip():
            ns = status.strip().upper()
            if ns == "ENABLED" and confirm_enable != "ENABLE":
                return _json({"error": "enable_confirmation_required", "message": "Pass confirm_enable='ENABLE' to enable."})
            if ns not in {"PAUSED", "ENABLED"}:
                return _json({"error": "unsupported_status", "message": "Supported statuses: PAUSED, ENABLED."})
            ag.status = _enum_value(client, "AdGroupStatusEnum", ns); changed.append("status")
        if int(cpc_bid_micros or 0) > 0:
            ag.cpc_bid_micros = int(cpc_bid_micros); changed.append("cpc_bid_micros")
        if not changed:
            return _json({"error": "no_updates", "message": "Pass name, status, or cpc_bid_micros."})
        operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, ag._pb))
        response = service.mutate_ad_groups(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "updated_ad_group_resource_name": response.results[0].resource_name, "changed_fields": changed})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def add_keywords(customer_id: str = "", ad_group_id: str = "", keywords_csv: str = "",
                 match_type: str = "PHRASE", cpc_bid_micros: int = 0, status: str = "ENABLED") -> str:
    """Add positive keywords to an ad group in one batch.

    match_type: EXACT, PHRASE, or BROAD. keywords_csv is comma-separated keyword text.
    Keywords don't spend on their own — the campaign/ad group gate serving, so ENABLED is
    the normal default here. cpc_bid_micros overrides the ad group bid per keyword (optional).
    """
    if not ad_group_id.strip():
        return _json({"error": "ad_group_id_required", "message": "ad_group_id is required."})
    keywords = _split_csv(keywords_csv)
    if not keywords:
        return _json({"error": "keywords_required", "message": "Pass at least one keyword in keywords_csv."})
    mt = (match_type or "PHRASE").strip().upper()
    if mt not in {"EXACT", "PHRASE", "BROAD"}:
        return _json({"error": "bad_match_type", "message": "match_type must be EXACT, PHRASE, or BROAD."})
    ns = (status or "ENABLED").strip().upper()
    if ns not in {"ENABLED", "PAUSED"}:
        ns = "ENABLED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AdGroupCriterionService")
        ag_path = _ad_group_path(client, cid, ad_group_id)
        operations = []
        for kw in keywords:
            operation = client.get_type("AdGroupCriterionOperation")
            crit = operation.create
            crit.ad_group = ag_path
            crit.status = _enum_value(client, "AdGroupCriterionStatusEnum", ns)
            crit.keyword.text = kw
            crit.keyword.match_type = _enum_value(client, "KeywordMatchTypeEnum", mt)
            if int(cpc_bid_micros or 0) > 0:
                crit.cpc_bid_micros = int(cpc_bid_micros)
            operations.append(operation)
        response = service.mutate_ad_group_criteria(customer_id=cid, operations=operations)
        return _json({"customer_id": cid, "added_keywords": len(response.results), "match_type": mt,
                      "resource_names": [r.resource_name for r in response.results]})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def add_negative_keywords(customer_id: str = "", ad_group_id: str = "", campaign_id: str = "",
                          keywords_csv: str = "", match_type: str = "PHRASE") -> str:
    """Add negative keywords at the ad group OR campaign level (pass exactly one of the ids).

    Campaign-level negatives apply to every ad group in the campaign. match_type: EXACT/PHRASE/BROAD.
    """
    if bool(ad_group_id.strip()) == bool(campaign_id.strip()):
        return _json({"error": "one_level_required", "message": "Pass exactly one of ad_group_id or campaign_id."})
    keywords = _split_csv(keywords_csv)
    if not keywords:
        return _json({"error": "keywords_required", "message": "Pass at least one keyword in keywords_csv."})
    mt = (match_type or "PHRASE").strip().upper()
    if mt not in {"EXACT", "PHRASE", "BROAD"}:
        return _json({"error": "bad_match_type", "message": "match_type must be EXACT, PHRASE, or BROAD."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        match_enum = _enum_value(client, "KeywordMatchTypeEnum", mt)
        if ad_group_id.strip():
            service = client.get_service("AdGroupCriterionService")
            ag_path = _ad_group_path(client, cid, ad_group_id)
            operations = []
            for kw in keywords:
                operation = client.get_type("AdGroupCriterionOperation")
                crit = operation.create
                crit.ad_group = ag_path
                crit.negative = True
                crit.keyword.text = kw
                crit.keyword.match_type = match_enum
                operations.append(operation)
            response = service.mutate_ad_group_criteria(customer_id=cid, operations=operations)
            level = "ad_group"
        else:
            service = client.get_service("CampaignCriterionService")
            camp_path = _campaign_path(client, cid, campaign_id)
            operations = []
            for kw in keywords:
                operation = client.get_type("CampaignCriterionOperation")
                crit = operation.create
                crit.campaign = camp_path
                crit.negative = True
                crit.keyword.text = kw
                crit.keyword.match_type = match_enum
                operations.append(operation)
            response = service.mutate_campaign_criteria(customer_id=cid, operations=operations)
            level = "campaign"
        return _json({"customer_id": cid, "level": level, "added_negatives": len(response.results),
                      "resource_names": [r.resource_name for r in response.results]})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def create_responsive_search_ad(customer_id: str = "", ad_group_id: str = "", final_url: str = "",
                                headlines_csv: str = "", descriptions_csv: str = "",
                                path1: str = "", path2: str = "", status: str = "PAUSED") -> str:
    """Create a Responsive Search Ad (RSA) in an ad group. Defaults to PAUSED.

    Google requires 3-15 headlines (<=30 chars each) and 2-4 descriptions (<=90 chars each).
    path1/path2 are the optional display-URL path segments (<=15 chars each).
    """
    if not ad_group_id.strip():
        return _json({"error": "ad_group_id_required", "message": "ad_group_id is required."})
    if not final_url.strip():
        return _json({"error": "final_url_required", "message": "final_url is required."})
    headlines = _split_csv(headlines_csv)
    descriptions = _split_csv(descriptions_csv)
    if len(headlines) < 3:
        return _json({"error": "need_3_headlines", "message": "Provide at least 3 headlines (up to 15)."})
    if len(descriptions) < 2:
        return _json({"error": "need_2_descriptions", "message": "Provide at least 2 descriptions (up to 4)."})
    ns = (status or "PAUSED").strip().upper()
    if ns not in {"PAUSED", "ENABLED"}:
        ns = "PAUSED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AdGroupAdService")
        operation = client.get_type("AdGroupAdOperation")
        ad_group_ad = operation.create
        ad_group_ad.ad_group = _ad_group_path(client, cid, ad_group_id)
        ad_group_ad.status = _enum_value(client, "AdGroupAdStatusEnum", ns)
        ad = ad_group_ad.ad
        ad.final_urls.append(final_url.strip())
        for text in headlines[:15]:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.headlines.append(asset)
        for text in descriptions[:4]:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.descriptions.append(asset)
        if path1.strip():
            ad.responsive_search_ad.path1 = path1.strip()
        if path2.strip():
            ad.responsive_search_ad.path2 = path2.strip()
        response = service.mutate_ad_group_ads(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "created_ad_resource_name": response.results[0].resource_name,
                      "created_status": ns, "headlines": len(headlines), "descriptions": len(descriptions)})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def add_geo_targets(customer_id: str = "", campaign_id: str = "", geo_target_constant_ids_csv: str = "") -> str:
    """Add location targeting to a campaign by geo target constant ID.

    IDs are Google's geo target constants, e.g. 2410 = South Korea, 2840 = United States,
    1009871 = Seoul. Find them with a GAQL query on geo_target_constant, or Google's
    geotargets reference. geo_target_constant_ids_csv is comma-separated numeric IDs.
    """
    if not campaign_id.strip():
        return _json({"error": "campaign_id_required", "message": "campaign_id is required."})
    ids = _split_csv(geo_target_constant_ids_csv)
    if not ids:
        return _json({"error": "geo_ids_required", "message": "Pass at least one geo target constant id."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("CampaignCriterionService")
        camp_path = _campaign_path(client, cid, campaign_id)
        operations = []
        for gid in ids:
            operation = client.get_type("CampaignCriterionOperation")
            crit = operation.create
            crit.campaign = camp_path
            crit.location.geo_target_constant = f"geoTargetConstants/{gid.replace('-', '').strip()}"
            operations.append(operation)
        response = service.mutate_campaign_criteria(customer_id=cid, operations=operations)
        return _json({"customer_id": cid, "added_geo_targets": len(response.results),
                      "resource_names": [r.resource_name for r in response.results]})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def add_language_targets(customer_id: str = "", campaign_id: str = "", language_constant_ids_csv: str = "") -> str:
    """Add language targeting to a campaign by language constant ID.

    IDs are Google's language constants, e.g. 1012 = Korean, 1000 = English, 1005 = Japanese.
    language_constant_ids_csv is comma-separated numeric IDs.
    """
    if not campaign_id.strip():
        return _json({"error": "campaign_id_required", "message": "campaign_id is required."})
    ids = _split_csv(language_constant_ids_csv)
    if not ids:
        return _json({"error": "language_ids_required", "message": "Pass at least one language constant id."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("CampaignCriterionService")
        camp_path = _campaign_path(client, cid, campaign_id)
        operations = []
        for lid in ids:
            operation = client.get_type("CampaignCriterionOperation")
            crit = operation.create
            crit.campaign = camp_path
            crit.language.language_constant = f"languageConstants/{lid.replace('-', '').strip()}"
            operations.append(operation)
        response = service.mutate_campaign_criteria(customer_id=cid, operations=operations)
        return _json({"customer_id": cid, "added_language_targets": len(response.results),
                      "resource_names": [r.resource_name for r in response.results]})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


# ---------------------------------------------------------------------------
# Demand Gen (image-based) — campaign, ad group, assets, and a multi-asset ad.
#
# STATUS: the image path (campaign -> ad group -> image/logo assets -> multi-asset ad ->
# validate) was created live against a real account and works. The video responsive ad is
# structurally validated but was not live-created (needs a real YouTube video id). Everything
# defaults to PAUSED so you create + inspect before enabling. Two hard constraints from the API:
#   - Bidding is conversion/click-based (no manual CPC). MAXIMIZE_CONVERSIONS and
#     TARGET_CPA need conversion tracking; MAXIMIZE_CLICKS works without it.
#   - Video must live on YouTube first; you reference it by video id, you cannot
#     upload raw video through this API. Images can be uploaded from a URL.
# ---------------------------------------------------------------------------


def _create_image_asset(cid: str, name: str, image_url: str, kind: str) -> str:
    """Shared: download an image URL (http/https, <=10 MB) and create a Google Ads IMAGE asset."""
    import urllib.request

    if not name.strip():
        return _json({"error": "name_required", "message": "Asset name is required."})
    if not image_url.lower().startswith(("http://", "https://")):
        return _json({"error": "bad_url", "message": "image_url must be an http(s) URL."})
    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": "google-ads-mcp/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            return _json({"error": "image_too_large", "message": "Image exceeds the 10 MB limit."})
        client = _build_client()
        service = client.get_service("AssetService")
        operation = client.get_type("AssetOperation")
        asset = operation.create
        asset.name = name.strip()
        asset.type_ = client.enums.AssetTypeEnum.IMAGE
        asset.image_asset.data = data
        response = service.mutate_assets(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "kind": kind, "image_asset_resource_name": response.results[0].resource_name, "bytes": len(data)})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def upload_image_asset(customer_id: str = "", name: str = "", image_url: str = "") -> str:
    """Create a marketing/creative IMAGE asset from a public image URL; returns its resource name.

    The server downloads image_url and uploads the bytes. Recommended sizes: landscape
    1200x628 (1.91:1), square 1200x1200 (1:1). Use the resource name in a Demand Gen ad.
    """
    return _create_image_asset((customer_id or DEFAULT_CUSTOMER_ID).replace("-", ""), name, image_url, "image")


@mcp.tool()
def upload_logo_asset(customer_id: str = "", name: str = "", image_url: str = "") -> str:
    """Create a LOGO image asset from a public image URL; returns its resource name.

    Logos are IMAGE assets like any other, but Demand Gen expects them in the logo slot.
    Recommended: square 1:1 (>=128x128) or 4:1 landscape logo. Use it in logo_image_asset_rns.
    """
    return _create_image_asset((customer_id or DEFAULT_CUSTOMER_ID).replace("-", ""), name, image_url, "logo")


@mcp.tool()
def attach_youtube_video_asset(customer_id: str = "", name: str = "", youtube_video_id: str = "") -> str:
    """Create a YOUTUBE_VIDEO asset from an existing YouTube video id (e.g. dQw4w9WgXcQ).

    You cannot upload raw video here — the video must already be on YouTube. Returns the
    asset resource name for use in create_demand_gen_video_responsive_ad.
    """
    if not name.strip():
        return _json({"error": "name_required", "message": "Asset name is required."})
    if not youtube_video_id.strip():
        return _json({"error": "video_id_required", "message": "youtube_video_id is required."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AssetService")
        operation = client.get_type("AssetOperation")
        asset = operation.create
        asset.name = name.strip()
        asset.type_ = client.enums.AssetTypeEnum.YOUTUBE_VIDEO
        asset.youtube_video_asset.youtube_video_id = youtube_video_id.strip()
        response = service.mutate_assets(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "youtube_video_asset_resource_name": response.results[0].resource_name})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def create_demand_gen_campaign(customer_id: str = "", name: str = "", budget_resource_name: str = "",
                               bidding: str = "MAXIMIZE_CLICKS", target_cpa_micros: int = 0,
                               status: str = "PAUSED", confirm_enable: str = "") -> str:
    """Create a DEMAND_GEN campaign. Defaults to PAUSED.

    bidding: MAXIMIZE_CLICKS (no conversion tracking needed), MAXIMIZE_CONVERSIONS, or
    TARGET_CPA (needs target_cpa_micros and conversion tracking). Requires a non-shared
    budget (create_campaign_budget already makes non-shared budgets).
    """
    if not name.strip():
        return _json({"error": "name_required", "message": "Campaign name is required."})
    if not budget_resource_name.strip():
        return _json({"error": "budget_required", "message": "budget_resource_name is required (non-shared)."})
    bid = (bidding or "MAXIMIZE_CLICKS").strip().upper()
    if bid not in {"MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS", "TARGET_CPA"}:
        return _json({"error": "bad_bidding", "message": "bidding must be MAXIMIZE_CLICKS, MAXIMIZE_CONVERSIONS, or TARGET_CPA."})
    ns = (status or "PAUSED").strip().upper()
    if ns == "ENABLED" and confirm_enable != "ENABLE":
        ns = "PAUSED"
    if ns not in {"PAUSED", "ENABLED"}:
        ns = "PAUSED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        campaign = operation.create
        campaign.name = name.strip()
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.DEMAND_GEN
        campaign.status = _enum_value(client, "CampaignStatusEnum", ns)
        campaign.campaign_budget = budget_resource_name.strip()
        # Required since Google Ads API v17+: declare EU political advertising status.
        campaign.contains_eu_political_advertising = client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        if bid == "MAXIMIZE_CLICKS":
            campaign.target_spend = client.get_type("TargetSpend")
        elif bid == "MAXIMIZE_CONVERSIONS":
            campaign.maximize_conversions = client.get_type("MaximizeConversions")
        else:  # TARGET_CPA
            if int(target_cpa_micros or 0) <= 0:
                return _json({"error": "target_cpa_required", "message": "TARGET_CPA needs a positive target_cpa_micros."})
            campaign.target_cpa.target_cpa_micros = int(target_cpa_micros)
        response = service.mutate_campaigns(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "created_campaign_resource_name": response.results[0].resource_name,
                      "bidding": bid, "created_status": ns})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def create_demand_gen_ad_group(customer_id: str = "", campaign_id: str = "", name: str = "",
                               status: str = "PAUSED") -> str:
    """Create an ad group under a Demand Gen campaign. Defaults to PAUSED."""
    if not campaign_id.strip():
        return _json({"error": "campaign_id_required", "message": "campaign_id is required."})
    if not name.strip():
        return _json({"error": "name_required", "message": "Ad group name is required."})
    ns = (status or "PAUSED").strip().upper()
    if ns not in {"PAUSED", "ENABLED"}:
        ns = "PAUSED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AdGroupService")
        operation = client.get_type("AdGroupOperation")
        ag = operation.create
        ag.name = name.strip()
        ag.campaign = _campaign_path(client, cid, campaign_id)
        ag.status = _enum_value(client, "AdGroupStatusEnum", ns)
        response = service.mutate_ad_groups(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "created_ad_group_resource_name": response.results[0].resource_name, "created_status": ns})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def create_demand_gen_multi_asset_ad(customer_id: str = "", ad_group_id: str = "", business_name: str = "",
                                     final_url: str = "", headlines_csv: str = "", descriptions_csv: str = "",
                                     marketing_image_asset_rns_csv: str = "", square_image_asset_rns_csv: str = "",
                                     logo_image_asset_rns_csv: str = "", call_to_action_text: str = "",
                                     status: str = "PAUSED") -> str:
    """Create an image-based Demand Gen ad (DemandGenMultiAssetAdInfo). Defaults to PAUSED.

    Provide image asset resource names from upload_image_asset:
      - marketing_image_asset_rns_csv: landscape 1.91:1 images (>=1)
      - square_image_asset_rns_csv: square 1:1 images (>=1)
      - logo_image_asset_rns_csv: logo images (>=1)
    headlines_csv up to 5, descriptions_csv up to 5, business_name and final_url required.
    call_to_action_text (optional) must be a display phrase like "Learn more", "Shop now",
    "Sign up", "Subscribe", "Book now", "Download" — NOT enum tokens like "LEARN_MORE".
    """
    if not ad_group_id.strip():
        return _json({"error": "ad_group_id_required", "message": "ad_group_id is required."})
    if not business_name.strip():
        return _json({"error": "business_name_required", "message": "business_name is required."})
    if not final_url.strip():
        return _json({"error": "final_url_required", "message": "final_url is required."})
    headlines = _split_csv(headlines_csv)
    descriptions = _split_csv(descriptions_csv)
    marketing = _split_csv(marketing_image_asset_rns_csv)
    squares = _split_csv(square_image_asset_rns_csv)
    logos = _split_csv(logo_image_asset_rns_csv)
    if not headlines:
        return _json({"error": "headlines_required", "message": "Provide at least one headline."})
    if not descriptions:
        return _json({"error": "descriptions_required", "message": "Provide at least one description."})
    if not (marketing or squares):
        return _json({"error": "images_required", "message": "Provide at least one marketing or square image asset."})
    if not logos:
        return _json({"error": "logo_required", "message": "Provide at least one logo image asset."})
    ns = (status or "PAUSED").strip().upper()
    if ns not in {"PAUSED", "ENABLED"}:
        ns = "PAUSED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AdGroupAdService")
        operation = client.get_type("AdGroupAdOperation")
        ad_group_ad = operation.create
        ad_group_ad.ad_group = _ad_group_path(client, cid, ad_group_id)
        ad_group_ad.status = _enum_value(client, "AdGroupAdStatusEnum", ns)
        ad = ad_group_ad.ad
        ad.final_urls.append(final_url.strip())
        info = ad.demand_gen_multi_asset_ad
        info.business_name = business_name.strip()  # string field in DemandGenMultiAssetAdInfo
        if call_to_action_text.strip():
            info.call_to_action_text = call_to_action_text.strip()

        def _img(rn: str):
            a = client.get_type("AdImageAsset")
            a.asset = rn
            return a

        def _txt(text: str):
            a = client.get_type("AdTextAsset")
            a.text = text
            return a

        for rn in marketing:
            info.marketing_images.append(_img(rn))
        for rn in squares:
            info.square_marketing_images.append(_img(rn))
        for rn in logos:
            info.logo_images.append(_img(rn))
        for h in headlines[:5]:
            info.headlines.append(_txt(h))
        for d in descriptions[:5]:
            info.descriptions.append(_txt(d))
        response = service.mutate_ad_group_ads(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "created_ad_resource_name": response.results[0].resource_name, "created_status": ns})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def create_demand_gen_video_responsive_ad(customer_id: str = "", ad_group_id: str = "", business_name: str = "",
                                          final_url: str = "", headlines_csv: str = "", long_headlines_csv: str = "",
                                          descriptions_csv: str = "", video_asset_rns_csv: str = "",
                                          logo_image_asset_rns_csv: str = "", status: str = "PAUSED") -> str:
    """Create a video-based Demand Gen ad (DemandGenVideoResponsiveAdInfo). Defaults to PAUSED.

    video_asset_rns_csv: YouTube video asset resource names from attach_youtube_video_asset (>=1).
    Needs >=1 headline, >=1 long_headline, >=1 description, >=1 logo, business_name, final_url.
    """
    if not ad_group_id.strip():
        return _json({"error": "ad_group_id_required", "message": "ad_group_id is required."})
    if not business_name.strip():
        return _json({"error": "business_name_required", "message": "business_name is required."})
    if not final_url.strip():
        return _json({"error": "final_url_required", "message": "final_url is required."})
    headlines = _split_csv(headlines_csv)
    long_headlines = _split_csv(long_headlines_csv)
    descriptions = _split_csv(descriptions_csv)
    videos = _split_csv(video_asset_rns_csv)
    logos = _split_csv(logo_image_asset_rns_csv)
    if not videos:
        return _json({"error": "video_required", "message": "Provide at least one YouTube video asset resource name."})
    if not headlines:
        return _json({"error": "headlines_required", "message": "Provide at least one headline."})
    if not long_headlines:
        return _json({"error": "long_headlines_required", "message": "Provide at least one long headline."})
    if not descriptions:
        return _json({"error": "descriptions_required", "message": "Provide at least one description."})
    if not logos:
        return _json({"error": "logo_required", "message": "Provide at least one logo image asset."})
    ns = (status or "PAUSED").strip().upper()
    if ns not in {"PAUSED", "ENABLED"}:
        ns = "PAUSED"
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("AdGroupAdService")
        operation = client.get_type("AdGroupAdOperation")
        ad_group_ad = operation.create
        ad_group_ad.ad_group = _ad_group_path(client, cid, ad_group_id)
        ad_group_ad.status = _enum_value(client, "AdGroupAdStatusEnum", ns)
        ad = ad_group_ad.ad
        ad.final_urls.append(final_url.strip())
        info = ad.demand_gen_video_responsive_ad
        info.business_name.text = business_name.strip()  # AdTextAsset in DemandGenVideoResponsiveAdInfo

        def _video(rn: str):
            a = client.get_type("AdVideoAsset")
            a.asset = rn
            return a

        def _img(rn: str):
            a = client.get_type("AdImageAsset")
            a.asset = rn
            return a

        def _txt(text: str):
            a = client.get_type("AdTextAsset")
            a.text = text
            return a

        for rn in videos:
            info.videos.append(_video(rn))
        for rn in logos:
            info.logo_images.append(_img(rn))
        for h in headlines[:5]:
            info.headlines.append(_txt(h))
        for h in long_headlines[:5]:
            info.long_headlines.append(_txt(h))
        for d in descriptions[:5]:
            info.descriptions.append(_txt(d))
        response = service.mutate_ad_group_ads(customer_id=cid, operations=[operation])
        return _json({"customer_id": cid, "created_ad_resource_name": response.results[0].resource_name, "created_status": ns})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


@mcp.tool()
def validate_created_demand_gen_ad(customer_id: str = "", ad_group_id: str = "") -> str:
    """Read back the ads in an ad group and report Google's policy review status.

    After creating a PAUSED Demand Gen ad, use this to confirm it passed review before
    enabling: returns each ad's approval_status (APPROVED / DISAPPROVED / AREA_OF_INTEREST_ONLY),
    review_status (REVIEW_IN_PROGRESS / REVIEWED / ...), and any policy topics that apply.
    """
    if not ad_group_id.strip():
        return _json({"error": "ad_group_id_required", "message": "ad_group_id is required."})
    try:
        cid = (customer_id or DEFAULT_CUSTOMER_ID).replace("-", "")
        client = _build_client()
        service = client.get_service("GoogleAdsService")
        query = (
            "SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.ad.type, "
            "ad_group_ad.status, ad_group_ad.policy_summary.approval_status, "
            "ad_group_ad.policy_summary.review_status "
            f"FROM ad_group_ad WHERE ad_group.id = {int(ad_group_id.replace('-', '').strip())}"
        )
        results = []
        for batch in service.search_stream(customer_id=cid, query=query):
            for row in batch.results:
                summary = row.ad_group_ad.policy_summary
                results.append({
                    "ad_id": row.ad_group_ad.ad.id,
                    "ad_name": row.ad_group_ad.ad.name,
                    "ad_type": _value(row.ad_group_ad.ad.type_),
                    "status": _value(row.ad_group_ad.status),
                    "approval_status": _value(summary.approval_status),
                    "review_status": _value(summary.review_status),
                    "policy_topics": [
                        {"topic": e.topic, "type": _value(e.type_)}
                        for e in summary.policy_topic_entries
                    ],
                })
        verdict = "ok" if results and all(r["approval_status"] in ("APPROVED", "AREA_OF_INTEREST_ONLY") for r in results) else "review_needed"
        return _json({"customer_id": cid, "ad_group_id": cid and ad_group_id, "verdict": verdict, "ad_count": len(results), "ads": results})
    except GoogleAdsException as ex:
        return _json(_google_ads_error(ex))
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)})


def self_test() -> int:
    print("Self-test: listing accessible customers via direct Google Ads API...", file=sys.stderr)
    result = list_accessible_customers()
    print(result)
    data = json.loads(result)
    return 0 if data.get("customers") else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    mcp.run(transport="stdio")  # local stdio mode; Cloud Run uses uvicorn on `app`
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
