"""
A/B Testing Pool — config-driven champion-vs-variant agent pools.

Provides:
- ABVariant dataclass for variant definitions
- ABPool for loading/validating config and sampling challengers
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_AB_AGENTS_DIR = "/root/archi/ab_agents"
DEFAULT_VARIANT_LABEL_MODE = "post_vote_reveal"
DEFAULT_ACTIVITY_PANEL_DEFAULT_STATE = "hidden"
DEFAULT_DISCLOSURE_MODE = DEFAULT_VARIANT_LABEL_MODE
DEFAULT_TRACE_MODE = DEFAULT_ACTIVITY_PANEL_DEFAULT_STATE


@dataclass(frozen=True)
class ABVariant:
    """A single agent variant in the A/B testing pool."""

    label: str
    agent_spec: str                           # filename under agents_dir
    provider: Optional[str] = None            # e.g. "anthropic", "openai"
    model: Optional[str] = None               # e.g. "claude-sonnet-4-20250514"
    num_documents_to_retrieve: Optional[int] = None
    recursion_limit: Optional[int] = None
    agent_spec_id: Optional[int] = None
    agent_spec_name: Optional[str] = None
    agent_spec_version_id: Optional[int] = None
    agent_spec_version_number: Optional[int] = None
    agent_spec_content_hash: Optional[str] = None
    agent_spec_tools: Optional[List[str]] = None
    agent_spec_prompt_hash: Optional[str] = None

    @property
    def name(self) -> str:
        """Backward-compatible alias for existing call-sites and stored metrics."""
        return self.label

    def to_meta(self) -> Dict[str, Any]:
        """Serialise variant config for JSONB storage in comparison records."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_meta_json(self) -> str:
        return json.dumps(self.to_meta(), default=str)


class ABPoolError(ValueError):
    """Raised when the ab_testing config is invalid."""
    pass


@dataclass(frozen=True)
class ABPoolLoadState:
    """Represents the runtime/admin state of the configured A/B pool."""

    pool: Optional["ABPool"]
    warnings: List[str] = field(default_factory=list)
    enabled_requested: bool = False
    agent_dir: str = DEFAULT_AB_AGENTS_DIR
    agent_dir_configured: bool = False


class ABPool:
    """
    Manages the set of agent variants and champion designation.

    Loaded from ``services.chat_app.ab_testing`` in config.yaml:

    .. code-block:: yaml

        services:
          chat_app:
            ab_testing:
              enabled: true
              pool:
                champion: "production-v2"
                variants:
                  - label: "production-v2"
                    agent_spec: "cms-comp-ops.md"
                    provider: "anthropic"
                    model: "claude-sonnet-4-20250514"
                  - label: "gpt4o-candidate"
                    agent_spec: "cms-comp-ops.md"
                    provider: "openai"
                    model: "gpt-4o"
    """

    VALID_DISCLOSURE_MODES = {"hidden", "post_vote_reveal", "always_visible"}
    VALID_TRACE_MODES = {"hidden", "collapsed", "expanded"}

    def __init__(
        self,
        variants: List[ABVariant],
        champion_name: str,
        *,
        enabled: bool = True,
        sample_rate: float = 1.0,
        target_roles: Optional[List[str]] = None,
        target_permissions: Optional[List[str]] = None,
        max_pending_per_conversation: int = 1,
        disclosure_mode: str = DEFAULT_DISCLOSURE_MODE,
        default_trace_mode: str = DEFAULT_TRACE_MODE,
    ) -> None:
        if len(variants) < 2:
            raise ABPoolError("ABPool requires at least 2 variants for A/B comparison.")
        if champion_name not in {v.label for v in variants}:
            raise ABPoolError(f"Champion '{champion_name}' not found in variant list.")
        if not 0 <= float(sample_rate) <= 1:
            raise ABPoolError("ab_testing.comparison_rate must be between 0 and 1.")
        if max_pending_per_conversation < 1:
            raise ABPoolError(
                "ab_testing.max_pending_comparisons_per_conversation must be at least 1."
            )
        if disclosure_mode not in self.VALID_DISCLOSURE_MODES:
            raise ABPoolError(
                f"ab_testing.variant_label_mode must be one of {sorted(self.VALID_DISCLOSURE_MODES)}."
            )
        if default_trace_mode not in self.VALID_TRACE_MODES:
            raise ABPoolError(
                "ab_testing.activity_panel_default_state must be one of "
                f"{sorted(self.VALID_TRACE_MODES)}."
            )
        self.variants = variants
        self.champion_name = champion_name
        self._variant_map: Dict[str, ABVariant] = {v.label: v for v in variants}
        self.enabled = bool(enabled)
        self.sample_rate = float(sample_rate)
        self.target_roles = [r for r in (target_roles or []) if isinstance(r, str) and r.strip()]
        self.target_permissions = [
            p for p in (target_permissions or []) if isinstance(p, str) and p.strip()
        ]
        self.max_pending_per_conversation = int(max_pending_per_conversation)
        self.disclosure_mode = disclosure_mode
        self.default_trace_mode = default_trace_mode

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, ab_config: Dict[str, Any]) -> "ABPool":
        """
        Build an ABPool from the ``services.chat_app.ab_testing`` config dict.

        Expected structure::

            services:
              chat_app:
                ab_testing:
                  enabled: true
                  pool:
                    champion: "variant-name"
                    variants:
                      - label: variant-name
                        agent_spec: variant-name.md
                        provider: local
                        model: "qwen3:32b"
                      - label: other-variant
                        agent_spec: other-variant.md
                        model: "gpt-oss:120b"

        Raises ABPoolError on validation failures.
        """
        if not isinstance(ab_config, dict):
            raise ABPoolError("ab_testing config must be a mapping.")

        pool_config = ab_config.get("pool")
        if not pool_config or not isinstance(pool_config, dict):
            raise ABPoolError("ab_testing.pool must be a mapping with 'champion' and 'variants'.")

        champion_name = _get_first_config_value(pool_config, "champion", "control")
        if isinstance(champion_name, str):
            champion_name = champion_name.strip()
        if not champion_name or not isinstance(champion_name, str):
            raise ABPoolError("ab_testing.pool.champion must be a non-empty string.")

        variant_list = pool_config.get("variants")
        if not variant_list or not isinstance(variant_list, list):
            raise ABPoolError("ab_testing.pool.variants must be a non-empty list of variants.")

        variants: List[ABVariant] = []
        seen_labels: set = set()
        for idx, entry in enumerate(variant_list):
            if not isinstance(entry, dict):
                raise ABPoolError(f"ab_testing.pool.variants[{idx}] must be a mapping.")
            if "name" in entry and "label" not in entry:
                raise ABPoolError(
                    f"ab_testing.pool.variants[{idx}] uses deprecated 'name'. "
                    "Use required fields 'label' and 'agent_spec'."
                )

            label = entry.get("label")
            if isinstance(label, str):
                label = label.strip()
            if not label or not isinstance(label, str):
                raise ABPoolError(f"ab_testing.pool.variants[{idx}] must include a string 'label'.")
            if label in seen_labels:
                raise ABPoolError(f"Duplicate variant label '{label}' in ab_testing.pool.variants.")
            seen_labels.add(label)

            agent_spec = entry.get("agent_spec")
            if isinstance(agent_spec, str):
                agent_spec = agent_spec.strip()
            if not agent_spec or not isinstance(agent_spec, str):
                raise ABPoolError(
                    f"ab_testing.pool.variants[{idx}] must include a string 'agent_spec'."
                )
            if Path(agent_spec).name != agent_spec:
                raise ABPoolError(
                    f"ab_testing.pool.variants[{idx}].agent_spec must be a filename under agents_dir."
                )

            variants.append(ABVariant(
                label=label,
                agent_spec=agent_spec,
                provider=entry.get("provider"),
                model=entry.get("model"),
                num_documents_to_retrieve=entry.get("num_documents_to_retrieve"),
                recursion_limit=entry.get("recursion_limit"),
                agent_spec_id=entry.get("agent_spec_id"),
                agent_spec_name=entry.get("agent_spec_name"),
                agent_spec_version_id=entry.get("agent_spec_version_id"),
                agent_spec_version_number=entry.get("agent_spec_version_number"),
                agent_spec_content_hash=entry.get("agent_spec_content_hash"),
                agent_spec_tools=entry.get("agent_spec_tools"),
                agent_spec_prompt_hash=entry.get("agent_spec_prompt_hash"),
            ))

        if champion_name not in seen_labels:
            raise ABPoolError(
                f"Champion '{champion_name}' not found in pool. "
                f"Available: {sorted(seen_labels)}"
            )

        if len(variants) < 2:
            raise ABPoolError("ab_testing.pool.variants must contain at least 2 variants for A/B comparison.")

        logger.info(
            "Loaded A/B pool: %d variants, champion='%s'",
            len(variants), champion_name,
        )
        return cls(
            variants=variants,
            champion_name=champion_name,
            enabled=ab_config.get("enabled", True),
            sample_rate=_get_first_config_value(ab_config, "comparison_rate", "sample_rate", default=1.0),
            target_roles=_get_first_config_value(ab_config, "eligible_roles", "target_roles", default=[]) or [],
            target_permissions=_get_first_config_value(
                ab_config, "eligible_permissions", "target_permissions", default=[]
            ) or [],
            max_pending_per_conversation=_get_first_config_value(
                ab_config,
                "max_pending_comparisons_per_conversation",
                "max_pending_per_conversation",
                default=1,
            ),
            disclosure_mode=normalize_ab_disclosure_mode(
                _get_first_config_value(
                    ab_config, "variant_label_mode", "disclosure_mode", default=DEFAULT_DISCLOSURE_MODE
                )
            ),
            default_trace_mode=normalize_ab_trace_mode(
                _get_first_config_value(
                    ab_config,
                    "activity_panel_default_state",
                    "default_trace_mode",
                    default=DEFAULT_TRACE_MODE,
                )
            ),
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def champion(self) -> ABVariant:
        return self._variant_map[self.champion_name]

    @property
    def control_name(self) -> str:
        return self.champion_name

    @property
    def comparison_rate(self) -> float:
        return self.sample_rate

    @property
    def eligible_roles(self) -> List[str]:
        return list(self.target_roles)

    @property
    def eligible_permissions(self) -> List[str]:
        return list(self.target_permissions)

    @property
    def max_pending_comparisons_per_conversation(self) -> int:
        return self.max_pending_per_conversation

    @property
    def variant_label_mode(self) -> str:
        return self.disclosure_mode

    @property
    def activity_panel_default_state(self) -> str:
        return self.default_trace_mode

    @property
    def challengers(self) -> List[ABVariant]:
        return [v for v in self.variants if v.label != self.champion_name]

    def get_variant(self, label: str) -> Optional[ABVariant]:
        return self._variant_map.get(label)

    def sample_challenger(self) -> ABVariant:
        """Return a random comparison variant that is not the champion."""
        pool = self.challengers
        if not pool:
            raise ABPoolError("No variants available for comparison (pool has only the champion).")
        return random.choice(pool)

    def sample_matchup(self) -> Tuple[ABVariant, ABVariant, bool]:
        """
        Return (arm_a_variant, arm_b_variant, is_champion_first).

        The champion is always one arm. Position is randomised.
        """
        challenger = self.sample_challenger()
        is_champion_first = random.random() < 0.5
        if is_champion_first:
            return self.champion, challenger, True
        else:
            return challenger, self.champion, False

    def is_targeted_user(self, roles: Optional[List[str]] = None, permissions: Optional[List[str]] = None) -> bool:
        roles_set = set(roles or [])
        permissions_set = set(permissions or [])
        if self.target_roles and not roles_set.intersection(self.target_roles):
            return False
        if self.target_permissions and not permissions_set.intersection(self.target_permissions):
            return False
        return True

    def should_sample(self) -> bool:
        if self.sample_rate <= 0:
            return False
        if self.sample_rate >= 1:
            return True
        return random.random() < self.sample_rate

    def pool_info(self) -> Dict[str, Any]:
        """Return serialisable pool metadata for the /api/ab/pool endpoint."""
        return {
            "enabled": self.enabled,
            "champion": self.champion_name,
            "variants": [v.label for v in self.variants],
            "variant_details": [v.to_meta() for v in self.variants],
            "variant_count": len(self.variants),
            "comparison_rate": self.comparison_rate,
            "eligible_roles": self.eligible_roles,
            "eligible_permissions": self.eligible_permissions,
            "max_pending_comparisons_per_conversation": self.max_pending_comparisons_per_conversation,
            "variant_label_mode": self.variant_label_mode,
            "activity_panel_default_state": self.activity_panel_default_state,
        }

    def participant_info(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "comparison_rate": self.comparison_rate,
            "variant_label_mode": self.variant_label_mode,
            "activity_panel_default_state": self.activity_panel_default_state,
            "max_pending_comparisons_per_conversation": self.max_pending_comparisons_per_conversation,
        }


def _get_first_config_value(mapping: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(mapping, dict) and key in mapping:
            return mapping.get(key)
    return default


def normalize_ab_disclosure_mode(value: Any) -> str:
    normalized = str(value or DEFAULT_DISCLOSURE_MODE).strip()
    if not normalized:
        return DEFAULT_DISCLOSURE_MODE
    if normalized == "reveal_after_vote":
        normalized = "post_vote_reveal"
    elif normalized == "show_during_streaming":
        normalized = "always_visible"
    if normalized not in ABPool.VALID_DISCLOSURE_MODES:
        raise ABPoolError(
            f"ab_testing.variant_label_mode must be one of {sorted(ABPool.VALID_DISCLOSURE_MODES)}."
        )
    return normalized


def normalize_ab_trace_mode(value: Any) -> str:
    normalized = str(value or DEFAULT_TRACE_MODE).strip()
    if not normalized:
        return DEFAULT_TRACE_MODE
    if normalized not in ABPool.VALID_TRACE_MODES:
        raise ABPoolError(
            "ab_testing.activity_panel_default_state must be one of "
            f"{sorted(ABPool.VALID_TRACE_MODES)}."
        )
    return normalized


def load_ab_pool(config: Dict[str, Any]) -> Optional[ABPool]:
    """
    Load the A/B pool from the full config dict.

    The only supported location is ``config["services"]["chat_app"]["ab_testing"]``.
    Legacy top-level ``ab_testing`` and ``services.ab_testing`` blocks are
    ignored.

    Returns None if the chat_app A/B config is not configured or disabled.
    """
    if isinstance(config.get("ab_testing"), dict):
        logger.warning("Ignoring deprecated top-level ab_testing config; use services.chat_app.ab_testing.")

    services = config.get("services") or {}
    if isinstance(services.get("ab_testing"), dict):
        logger.warning("Ignoring deprecated services.ab_testing config; use services.chat_app.ab_testing.")

    return load_ab_pool_state(config).pool


def resolve_ab_agents_dir(chat_app_config: Dict[str, Any]) -> Tuple[Path, bool]:
    """Return the resolved A/B agent-spec directory and whether it was explicitly configured."""
    ab_cfg = (chat_app_config or {}).get("ab_testing") or {}
    raw_dir = ab_cfg.get("ab_agents_dir")
    if isinstance(raw_dir, str) and raw_dir.strip():
        return Path(raw_dir.strip()).expanduser(), True
    return Path(DEFAULT_AB_AGENTS_DIR), False


def load_ab_pool_state(
    config: Dict[str, Any],
    *,
    agent_spec_exists: Optional[Callable[[str], bool]] = None,
) -> ABPoolLoadState:
    """
    Inspect the chat_app A/B configuration and return both the active pool
    (when valid) and non-fatal warnings for incomplete setup.
    """
    warnings: List[str] = []

    if isinstance(config.get("ab_testing"), dict):
        logger.warning("Ignoring deprecated top-level ab_testing config; use services.chat_app.ab_testing.")

    services = config.get("services") or {}
    if isinstance(services.get("ab_testing"), dict):
        logger.warning("Ignoring deprecated services.ab_testing config; use services.chat_app.ab_testing.")

    chat_app = services.get("chat_app") or {}
    agent_dir, configured = resolve_ab_agents_dir(chat_app)
    ab_config = chat_app.get("ab_testing")
    if not ab_config or not isinstance(ab_config, dict):
        return ABPoolLoadState(pool=None, warnings=[], enabled_requested=False, agent_dir=str(agent_dir), agent_dir_configured=configured)

    enabled_requested = bool(ab_config.get("enabled", False))
    if not enabled_requested:
        return ABPoolLoadState(pool=None, warnings=warnings, enabled_requested=False, agent_dir=str(agent_dir), agent_dir_configured=configured)

    try:
        pool = ABPool.from_config(ab_config)
    except ABPoolError as exc:
        warnings.append(
            "A/B testing is enabled but inactive until configuration is completed in the admin UI. "
            f"{exc}"
        )
        return ABPoolLoadState(pool=None, warnings=warnings, enabled_requested=True, agent_dir=str(agent_dir), agent_dir_configured=configured)

    if agent_spec_exists is None:
        missing_specs = [
            variant.agent_spec
            for variant in pool.variants
            if not (agent_dir / variant.agent_spec).exists()
        ]
    else:
        missing_specs = [
            variant.agent_spec
            for variant in pool.variants
            if not agent_spec_exists(variant.agent_spec)
        ]
    if missing_specs:
        warnings.append(
            f"A/B testing is enabled but inactive because the A/B agent pool is missing: {sorted(missing_specs)}."
        )
        return ABPoolLoadState(pool=None, warnings=warnings, enabled_requested=True, agent_dir=str(agent_dir), agent_dir_configured=configured)

    return ABPoolLoadState(
        pool=pool,
        warnings=warnings,
        enabled_requested=True,
        agent_dir=str(agent_dir),
        agent_dir_configured=configured,
    )
