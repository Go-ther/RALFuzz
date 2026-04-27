from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_PROMPT_TEMPLATE = """/*
Library: {library_name} (version: {library_version})
Target: {api_signature}
Header: <{header}>
Exec: init={execution_init_path}; cleanup={execution_cleanup_path}; neighbors={execution_neighbor_apis}; chain={execution_short_call_chain}
Risk: level={risk_level}; tags={risk_tags}; hints={risk_boundary_hints}; history={risk_history_summary}; high-risk={risk_high_risk_neighbors}

Write one standalone C11 harness:
- include -> init if needed -> build short input -> call `{api_name}` -> cleanup if needed -> print status
- must contain `int main(void)`
- must call `{api_name}` at least once
- prefer concise code, at most one helper function
- avoid long comments, markdown fences, and giant buffers
- output code only
*/
"""


@dataclass
class ApiSpec:
    api_name: str
    api_signature: str
    header: str
    doc_url: Optional[str] = None


@dataclass
class ExecutionContext:
    init_path: List[str] = field(default_factory=list)
    cleanup_path: List[str] = field(default_factory=list)
    neighbor_apis: List[str] = field(default_factory=list)
    short_call_chain_template: List[str] = field(default_factory=list)
    source_files_scanned: int = 0


@dataclass
class RiskContext:
    risk_level: str = "low"
    risk_tags: List[str] = field(default_factory=list)
    boundary_hints: List[str] = field(default_factory=list)
    history_summary: str = ""
    high_risk_neighbors: List[str] = field(default_factory=list)


@dataclass
class SeedValidation:
    valid: bool
    reason: str
    syntax_fixed_code: str
    compile_success: bool = False
    run_success: bool = False
    target_hit: bool = False
    init_hit: bool = False
    cleanup_hit: bool = False
    risk_hit: bool = False
    risk_markers: List[str] = field(default_factory=list)
    raw_risk_hit: bool = False
    aligned_risk_families: List[str] = field(default_factory=list)
    expected_risk_families: List[str] = field(default_factory=list)
    aligned_risk_score: float = 0.0
    runtime_hits_observed: bool = False
    target_count: int = 0
    init_count: int = 0
    cleanup_count: int = 0
    compile_msg: str = ""
    run_msg: str = ""
