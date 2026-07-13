"""Agent tool for ingesting a URL into the catalog.

Wraps the data-manager's ``POST /document_index/upload_url`` endpoint. A pluggable
list of *routing rules* (regex → action) is evaluated against every URL before
the request goes out, so deployments can:

* **refuse** specific URLs at the agent layer and redirect the model to a more
  appropriate tool (e.g. send Indico event URLs to ``ingest_indico_event``,
  which uses the authenticated Indico MCP path);
* **retry via SSO**: opt in (off by default) to having the data-manager fall
  back to a Selenium-based ``CERNSSOScraper`` when the anonymous LinkScraper
  hits a Keycloak login redirect. The data-manager owns the actual retry; the
  agent only sets the ``allow_sso_fallback`` flag on its request.

Both the rule list and the ``sso_fallback_enabled`` toggle are read from
``services.chat_app.tools.ingest_url`` in the deployment config. If no config
is supplied, the built-in :data:`DEFAULT_ROUTING_RULES` preserves today's
Indico-refusal behavior so callers using MCP keep getting the deterministic
``ingest_indico_event`` redirect.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import requests
from langchain_core.tools import tool

from src.archi.pipelines.agents.tools.base import require_tool_permission
from src.utils.logging import get_logger

logger = get_logger(__name__)


# Rule schema (each rule is a plain dict so config can be passed verbatim from YAML):
#
#   pattern: str            # regex matched against the full URL
#   action:  str            # "refuse" or "sso_retry"
#   scraper: str (optional) # informational tag; one of "indico_mcp", "sso", "link"
#   message: str (optional) # for action=refuse, the message returned to the agent.
#                           # Supports str.format() placeholders: {url} and any
#                           # numbered/named regex groups from `pattern`.
#
# Rules are evaluated in declaration order; the first match wins.
#
# Built-in defaults: Indico event URLs are refused with a pointer to
# ``ingest_indico_event``. CERN-domain URLs would benefit from SSO retry but the
# rule is opt-in: set sso_fallback_enabled=true and add an sso_retry rule in
# config to enable it.
DEFAULT_ROUTING_RULES: List[Dict[str, str]] = [
    {
        "pattern": r"^https?://[^/]*indico\.[^/]*/event/(?P<event_id>\d+)",
        "action": "refuse",
        "scraper": "indico_mcp",
        "message": (
            "Error: this URL is an Indico event page (event_id={event_id}). "
            "`ingest_url` cannot authenticate against CERN SSO and would store "
            "the login redirect page. Call "
            '`ingest_indico_event(event_id="{event_id}")` instead. '
            "That tool drives the bearer-authenticated Indico MCP server to "
            "download the event's attachments, then ingests them into the "
            "catalog. After it returns, use `search_metadata_index` with "
            "`event_id:{event_id}` to retrieve them."
        ),
    },
    {
        "pattern": r"^https?://[^/]*indico\.[^/]*/export/event/(?P<event_id>\d+)\.json",
        "action": "refuse",
        "scraper": "indico_mcp",
        "message": (
            "Error: this URL is an Indico API export (event_id={event_id}). "
            'Call `ingest_indico_event(event_id="{event_id}")` instead.'
        ),
    },
]


def _compile_rules(rules: List[Dict[str, Any]]) -> List[Tuple[re.Pattern, Dict[str, Any]]]:
    """Pre-compile pattern regexes; skip + log rules with invalid regex/action."""
    compiled: List[Tuple[re.Pattern, Dict[str, Any]]] = []
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            logger.warning("ingest_url routing rule #%d is not a dict; skipping", idx)
            continue
        pattern = rule.get("pattern")
        action = rule.get("action")
        if not pattern or action not in {"refuse", "sso_retry"}:
            logger.warning(
                "ingest_url routing rule #%d invalid (pattern=%r, action=%r); skipping",
                idx, pattern, action,
            )
            continue
        try:
            compiled.append((re.compile(pattern), rule))
        except re.error as exc:
            logger.warning(
                "ingest_url routing rule #%d has invalid regex %r: %s; skipping",
                idx, pattern, exc,
            )
    return compiled


def _match_rule(
    url: str,
    compiled_rules: List[Tuple[re.Pattern, Dict[str, Any]]],
) -> Optional[Tuple[Dict[str, Any], re.Match]]:
    """Return the first (rule, match) whose pattern matches *url*, or None."""
    for pattern, rule in compiled_rules:
        match = pattern.search(url)
        if match:
            return rule, match
    return None


def _render_refuse_message(rule: Dict[str, Any], match: re.Match, url: str) -> str:
    """Format a refusal message with {url} plus any named/positional regex groups."""
    template = rule.get("message") or (
        "Error: URL {url} is refused by ingest_url routing rule "
        f"({rule.get('scraper', 'unknown')}). See deployment config "
        "services.chat_app.tools.ingest_url.routing_rules."
    )
    fmt: Dict[str, Any] = dict(match.groupdict())
    fmt["url"] = url
    try:
        return template.format(*match.groups(), **fmt)
    except (IndexError, KeyError) as exc:
        logger.warning("ingest_url refuse message template error %s; using raw", exc)
        return template


def create_ingest_url_tool(
    data_manager_url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    name: str = "ingest_url",
    description: Optional[str] = None,
    timeout_seconds: float = 600.0,
    required_permission: Optional[str] = None,
    store_tool_input: Optional[Callable[[str, object], None]] = None,
    routing_rules: Optional[List[Dict[str, Any]]] = None,
    sso_fallback_enabled: bool = False,
) -> Callable[..., str]:
    """Build a LangChain tool that POSTs a URL to data-manager for ingestion.

    Args:
        data_manager_url: Base URL of the data-manager service.
        headers: Auth/extra headers forwarded on every request.
        name, description, timeout_seconds, required_permission: usual LangChain bits.
        store_tool_input: optional sink that records the bound tool args so they
            appear in the agent's trace UI.
        routing_rules: pluggable list of regex→action rules; see module docstring.
            ``None`` uses :data:`DEFAULT_ROUTING_RULES`. Pass ``[]`` to disable
            all built-in refusals (not recommended unless you have a better
            mechanism for routing Indico URLs to ``ingest_indico_event``).
        sso_fallback_enabled: when True, an ``sso_retry`` rule match causes the
            tool to add ``allow_sso_fallback=true`` to the POST body, asking the
            data-manager to retry via the Selenium ``CERNSSOScraper`` if the
            anonymous LinkScraper hits a Keycloak login page. Off by default.
    """

    tool_description = description or (
        "Ingest a URL into the knowledge base so it becomes searchable.\n"
        "The agent has a list of routing rules (operator-configurable) that may "
        "redirect specific URL patterns to other tools — e.g. Indico event URLs "
        "are refused here and you should use `ingest_indico_event(event_id=<id>)` "
        "instead. If a refusal is returned, follow the redirect — don't retry.\n"
        "Input: url (string). Optional: depth (int >= 1) for link-scraper crawl depth.\n"
        "Output: a short status string with the number of resources ingested.\n"
        "After a successful ingest, retrieve the new content DETERMINISTICALLY "
        "via `search_metadata_index` with `url:<the URL you just ingested>`, then "
        "`fetch_catalog_document` by hash. Do NOT loop on search_vectorstore_hybrid "
        "to find a freshly ingested URL — different phrasings rarely surface "
        "newly added pages."
    )

    upload_endpoint = f"{data_manager_url.rstrip('/')}/document_index/upload_url"
    request_headers = dict(headers or {})

    effective_rules = DEFAULT_ROUTING_RULES if routing_rules is None else routing_rules
    compiled_rules = _compile_rules(effective_rules)
    logger.info(
        "ingest_url: %d routing rule(s) active; sso_fallback_enabled=%s",
        len(compiled_rules), sso_fallback_enabled,
    )

    @tool(name, description=tool_description)
    @require_tool_permission(required_permission)
    def _ingest_url(url: str, depth: Optional[int] = None) -> str:
        url = (url or "").strip()
        if not url:
            return "Error: please provide a non-empty URL."

        if store_tool_input:
            try:
                store_tool_input(name, {"url": url, "depth": depth})
            except Exception:
                logger.debug("Failed to record tool input for %s", name, exc_info=True)

        matched = _match_rule(url, compiled_rules)
        request_allow_sso = False
        if matched is not None:
            rule, match = matched
            action = rule.get("action")
            if action == "refuse":
                return _render_refuse_message(rule, match, url)
            if action == "sso_retry":
                # The agent gate: the rule says THIS URL is a candidate for SSO
                # fallback; the global toggle says whether to actually request it.
                if sso_fallback_enabled:
                    request_allow_sso = True
                else:
                    logger.debug(
                        "ingest_url: %s matched sso_retry rule but sso_fallback_enabled=False",
                        url,
                    )

        form: dict[str, str] = {"url": url}
        if depth is not None:
            form["depth"] = str(depth)
        if request_allow_sso:
            form["allow_sso_fallback"] = "true"

        try:
            resp = requests.post(
                upload_endpoint,
                data=form,
                headers=request_headers,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("ingest_url request to %s failed: %s", upload_endpoint, exc)
            return f"Error: data-manager unreachable at {upload_endpoint}: {exc}"

        if resp.status_code != 200:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text[:500]
            return f"Error: data-manager returned {resp.status_code}: {detail}"

        try:
            payload = resp.json()
        except ValueError:
            return f"Ingested {url} (data-manager returned non-JSON: {resp.text[:200]})"

        count = payload.get("resources_scraped")
        scraper_used = payload.get("scraper")  # set by the data-manager when SSO retry fires
        followup = (
            f" To retrieve the new content, call search_metadata_index with "
            f"`url:{url}` (exact match against the resource's metadata.url), "
            f"then fetch_catalog_document by hash. Do NOT loop on "
            f"search_vectorstore_hybrid — it will not reliably surface a "
            f"freshly ingested URL across the full corpus."
        )
        scraper_suffix = f" [scraper={scraper_used}]" if scraper_used else ""
        if count is None:
            return f"Ingested {url} ({payload})." + followup
        if count == 0:
            return (
                f"Ingested {url}: 0 resource(s) scraped{scraper_suffix}. The page "
                f"may be empty, unreachable, or behind auth — do not retry the same URL."
            )
        return f"Ingested {url}: {count} resource(s) scraped and indexed{scraper_suffix}." + followup

    return _ingest_url
