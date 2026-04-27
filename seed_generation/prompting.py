from __future__ import annotations

from typing import List, Sequence

from seed_types import ApiSpec, ExecutionContext, RiskContext

RISK_BOUNDARY_LITERAL_HINTS: Sequence[str] = ("NULL", "\"\"", "-1", "INT_MAX", "INT_MIN", "SIZE_MAX")

def inline_items(items: Sequence[str], limit: int | None = None) -> str:
    if not items:
        return "(none)"
    values = list(items[:limit]) if limit else list(items)
    text = ", ".join(x.strip() for x in values if x and x.strip())
    return text or "(none)"


def compact_hints(items: Sequence[str], limit: int | None = None) -> str:
    if not items:
        return "(none)"
    values = list(items[:limit]) if limit else list(items)
    cleaned = [x.strip().rstrip(".") for x in values if x and x.strip()]
    return " | ".join(cleaned) if cleaned else "(none)"


def compact_text(text: str) -> str:
    text = " ".join((text or "").split())
    return text or "(none)"


def build_risk_constraint_lines(spec: ApiSpec, risk_context: RiskContext, min_marker_kinds: int, require_boundary_value: bool, require_high_risk_neighbor: bool) -> List[str]:
    min_k = max(1, int(min_marker_kinds))
    marker_targets = "malformed/truncated input, deep nesting, binary/hex payload, numeric extremes, pointer reuse"
    lines: List[str] = [
        f"- Include at least {min_k} short risk probes from: {marker_targets}.",
        "- Keep the program concise: at most one helper, short inputs, no long comments.",
        "- Do not generate only happy-path inputs; print at least one status/result.",
    ]
    if require_boundary_value:
        lines.append(f"- Include at least one explicit boundary literal from: {', '.join(RISK_BOUNDARY_LITERAL_HINTS)}.")
    if require_high_risk_neighbor and risk_context.high_risk_neighbors:
        hard_neighbors = ", ".join(risk_context.high_risk_neighbors[:2])
        lines.append(f"- If practical, besides `{spec.api_name}` also call one of: {hard_neighbors}.")
    return lines


def build_prompt(
    template: str,
    library_name: str,
    library_version: str,
    spec: ApiSpec,
    execution_context: ExecutionContext,
    risk_context: RiskContext,
    include_risk_card: bool = True,
    risk_prompt_hardening: bool = True,
    risk_min_marker_kinds: int = 2,
    risk_require_boundary_value: bool = True,
    risk_require_high_risk_neighbor: bool = True,
) -> str:
    effective_risk_context = risk_context if include_risk_card else RiskContext()
    rendered = template.format(
        library_name=library_name,
        library_version=library_version,
        header=spec.header,
        api_signature=spec.api_signature,
        api_name=spec.api_name,
        execution_init_path=inline_items(execution_context.init_path, limit=2),
        execution_cleanup_path=inline_items(execution_context.cleanup_path, limit=2),
        execution_neighbor_apis=inline_items(execution_context.neighbor_apis, limit=3),
        execution_short_call_chain=" -> ".join(execution_context.short_call_chain_template) or spec.api_name,
        risk_level=effective_risk_context.risk_level,
        risk_tags=inline_items(effective_risk_context.risk_tags, limit=3),
        risk_boundary_hints=compact_hints(effective_risk_context.boundary_hints, limit=2),
        risk_history_summary=compact_text(effective_risk_context.history_summary),
        risk_high_risk_neighbors=inline_items(effective_risk_context.high_risk_neighbors, limit=2),
    )
    if not include_risk_card:
        rendered = "\n".join(line for line in rendered.splitlines() if not line.startswith("Risk:")) + "\n"
    low = rendered.lower()
    if "markdown" not in low and "code fence" not in low:
        rendered += "\n/* Output plain C source only. Do not use markdown code fences. */\n"
    if include_risk_card and risk_prompt_hardening:
        risk_lines = build_risk_constraint_lines(
            spec,
            risk_context,
            min_marker_kinds=risk_min_marker_kinds,
            require_boundary_value=risk_require_boundary_value,
            require_high_risk_neighbor=risk_require_high_risk_neighbor,
        )
        rendered += "\n/* Risk hard constraints (must satisfy):\n" + "\n".join(risk_lines) + "\n*/\n"
    return rendered


def build_risk_retry_prompt(
    base_prompt: str,
    spec: ApiSpec,
    risk_context: RiskContext,
    min_marker_kinds: int,
    require_boundary_value: bool,
    require_high_risk_neighbor: bool,
) -> str:
    risk_lines = build_risk_constraint_lines(
        spec,
        risk_context,
        min_marker_kinds=min_marker_kinds,
        require_boundary_value=require_boundary_value,
        require_high_risk_neighbor=require_high_risk_neighbor,
    )
    retry_lines = [
        "Retry: previous candidate did not show enough risk coverage.",
        "Regenerate a NEW concise standalone C harness that still compiles and runs.",
    ]
    retry_lines.extend(risk_lines)
    return base_prompt + "\n/* " + "\n".join(retry_lines) + "\n*/\n"


def build_truncation_retry_prompt(
    base_prompt: str,
    spec: ApiSpec,
    risk_context: RiskContext,
    max_lines: int,
    min_marker_kinds: int,
    require_boundary_value: bool,
    require_high_risk_neighbor: bool,
) -> str:
    risk_lines = build_risk_constraint_lines(
        spec,
        risk_context,
        min_marker_kinds=min_marker_kinds,
        require_boundary_value=require_boundary_value,
        require_high_risk_neighbor=require_high_risk_neighbor,
    )
    lines = [
        "Retry: previous output looked truncated or incomplete.",
        "Regenerate a NEW complete C program with conservative structure.",
        f"- Keep the full program within about {max(40, int(max_lines))} lines.",
        "- Use at most one helper function and avoid long comments.",
        "- Keep each test input short; avoid huge string literals.",
        "- Ensure all quotes, braces, and parentheses are fully closed.",
        "- End with a complete `return 0;` and final closing brace.",
    ]
    lines.extend(risk_lines)
    return base_prompt + "\n/* " + "\n".join(lines) + "\n*/\n"
