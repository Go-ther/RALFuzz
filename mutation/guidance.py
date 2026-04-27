from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from ctitanfuzz.c_mutators import DepthFinder, UniqueFinder, find_call_spans
from ctitanfuzz.metadata import LibraryMetadata, score_distance
from ctitanfuzz.util.util import ExecutionStatus


@dataclass
class HarnessState:
    target_api: str
    called_apis: list[str]
    call_skeleton: list[str]
    unique_api_count: int
    call_chain_len: int
    max_depth: int
    exact_repeats: int
    repeated_apis: int
    target_hit: bool
    risk_hits: list[str]
    risk_path_hits: list[str]
    boundary_hint_hits: list[str]
    construction_tags: list[str]


@dataclass
class MutationFeedback:
    compile_ok: bool
    run_ok: bool
    coverage_gain: bool
    target_hit: bool
    risk_hits: list[str] = field(default_factory=list)
    risk_path_hits: list[str] = field(default_factory=list)
    boundary_hint_hits: list[str] = field(default_factory=list)
    sanitizer_hits: list[str] = field(default_factory=list)
    crash: bool = False
    timeout: bool = False
    reward: float = 0.0


class RiskGuidance:
    def __init__(self, metadata: LibraryMetadata):
        self.metadata = metadata
        self.api_names = sorted(metadata.api_specs, key=len, reverse=True)
        self.unique_finder = UniqueFinder(self.api_names)
        self.depth_finder = DepthFinder(self.api_names)

    def extract_harness_state(self, code: str, target_api: str) -> HarnessState:
        spans = find_call_spans(code, self.api_names)
        called_apis = [span.api_name for span in spans]
        call_skeleton = self._build_call_skeleton(called_apis)
        unique_calls, exact_repeats, repeated_apis = self.unique_finder.count(code)
        max_depth = self.depth_finder.max_depth(code)
        target_hit = target_api in called_apis
        risk_hits = []
        for api_name in dict.fromkeys(called_apis):
            profile = self.metadata.get_risk_profile(api_name)
            if profile is not None and profile.risk_level >= 0.6:
                risk_hits.append(api_name)
        boundary_hint_hits = self._find_boundary_hits(code, target_api)
        risk_path_hits = self._find_risk_paths(called_apis, target_api)
        construction_tags = self._find_construction_tags(code)
        return HarnessState(
            target_api=target_api,
            called_apis=called_apis,
            call_skeleton=call_skeleton,
            unique_api_count=unique_calls,
            call_chain_len=len(called_apis),
            max_depth=max_depth,
            exact_repeats=exact_repeats,
            repeated_apis=repeated_apis,
            target_hit=target_hit,
            risk_hits=risk_hits,
            risk_path_hits=risk_path_hits,
            boundary_hint_hits=boundary_hint_hits,
            construction_tags=construction_tags,
        )

    def behavior_signature_from_state(self, state: HarnessState) -> str:
        unique_apis = list(dict.fromkeys(state.called_apis))
        target_call_count = sum(1 for api_name in state.called_apis if api_name == state.target_api)
        signature = {
            "apis": unique_apis,
            "call_skeleton": state.call_skeleton[:6],
            "target_calls": min(target_call_count, 4),
            "depth": min(state.max_depth, 4),
            "risk_hits": sorted(set(state.risk_hits)),
            "risk_paths": sorted(set(state.risk_path_hits)),
            "boundary_hints": sorted(set(state.boundary_hint_hits)),
            "construction_tags": sorted(set(state.construction_tags)),
        }
        return json.dumps(signature, sort_keys=True)

    def behavior_signature(self, code: str, target_api: str) -> str:
        return self.behavior_signature_from_state(self.extract_harness_state(code, target_api))

    def _build_call_skeleton(self, called_apis: list[str]) -> list[str]:
        skeleton: list[str] = []
        last_api = None
        same_run = 0
        for api_name in called_apis:
            if api_name == last_api:
                same_run += 1
                if same_run >= 2:
                    continue
            else:
                last_api = api_name
                same_run = 0
            skeleton.append(api_name)
            if len(skeleton) >= 6:
                break
        return skeleton

    def _find_boundary_hits(self, code: str, target_api: str) -> list[str]:
        lower = code.lower()
        profile = self.metadata.get_risk_profile(target_api)
        if profile is None:
            return []
        hits: list[str] = []
        for hint in profile.boundary_hints:
            if hint == "null" and "null" in lower:
                hits.append(hint)
            elif hint == "empty" and ('""' in code or "strlen(input_text)" in code):
                hits.append(hint)
            elif hint == "oversized" and any(token in lower for token in ("1024", "4096", "size_max", "strlen(input_text) + 1")):
                hits.append(hint)
            elif hint == "truncated" and any(token in lower for token in ("- 1", " / 2", "trunc")):
                hits.append(hint)
            elif hint == "mismatch" and "strlen(input_text)" in code and "+ 1" in code:
                hits.append(hint)
            elif hint == "negative" and "-1" in code:
                hits.append(hint)
            elif hint == "max" and any(token in lower for token in ("size_max", "int_max", "uint_max")):
                hits.append(hint)
            elif hint == "unterminated" and "mutable_buffer" in code:
                hits.append(hint)
            elif hint == "malformed-input" and any(token in code for token in ('"{', '"<', '"seed')):
                hits.append(hint)
        return list(dict.fromkeys(hits))

    def _find_construction_tags(self, code: str) -> list[str]:
        tags: list[str] = []
        lower = code.lower()
        if re.search(r"\b(?:char|unsigned char)\s+\w+\s*\[\s*\d+\s*\]", code):
            tags.append("stack-buffer")
        if any(token in lower for token in ("malloc(", "calloc(", "realloc(")):
            tags.append("heap-buffer")
        if "snprintf(" in lower:
            tags.append("snprintf")
        if re.search(r"(?<!sn)sprintf\s*\(", lower):
            tags.append("sprintf")
        if "strcat(" in lower and any(token in lower for token in ("for (", "while (")):
            tags.append("loop-concat")
        if re.search(r"\bunsigned char\b", code) and re.search(r"0x[0-9a-fA-F]+", code):
            tags.append("binary-literal")
        if "strlen(" in lower or "input_length" in lower:
            tags.append("length-aware")
        if "parse_end" in lower:
            tags.append("parse-end")
        if re.search(r'const char \*\s*\w+\s*=\s*"', code):
            tags.append("string-literal")
        return list(dict.fromkeys(tags))

    def _contains_subsequence(self, sequence: list[str], pattern: list[str]) -> bool:
        if not pattern:
            return True
        pos = 0
        for item in sequence:
            if item == pattern[pos]:
                pos += 1
                if pos == len(pattern):
                    return True
        return False

    def _find_risk_paths(self, called_apis: list[str], target_api: str) -> list[str]:
        cg = self.metadata.get_call_graph_entry(target_api)
        if cg is None:
            return []
        hits: list[str] = []
        for neighbor in cg.neighbors:
            if neighbor in called_apis:
                hits.append("{}->{}".format(target_api, neighbor))
        for chain in cg.short_call_chains:
            if self._contains_subsequence(called_apis, chain):
                hits.append("->".join(chain))
        return list(dict.fromkeys(hits))

    def score_seed(self, code: str, target_api: str, feedback: MutationFeedback | None = None) -> float:
        state = self.extract_harness_state(code, target_api)
        struct_score = (
            state.unique_api_count
            + 0.8 * state.max_depth
            + 0.2 * state.call_chain_len
            - 0.6 * state.exact_repeats
            - 0.3 * state.repeated_apis
        )
        risk_score = 0.0
        for api_name in state.risk_hits:
            profile = self.metadata.get_risk_profile(api_name)
            if profile is not None:
                risk_score += profile.risk_level
        target_profile = self.metadata.get_risk_profile(target_api)
        target_cg = self.metadata.get_call_graph_entry(target_api)
        if target_profile is not None:
            risk_score += 0.5 * target_profile.risk_level
        if target_cg is not None:
            risk_score += 0.8 * score_distance(target_cg.distance_to_risky_region)
        risk_score += 0.35 * len(state.boundary_hint_hits) + 1.1 * len(state.risk_path_hits)

        exec_score = 0.3 if state.target_hit else -0.8
        if feedback is not None:
            exec_score += 0.8 if feedback.compile_ok else -0.4
            exec_score += 0.9 if feedback.run_ok else 0.0
            exec_score += 0.5 if feedback.coverage_gain else 0.0
            exec_score += 0.9 if feedback.sanitizer_hits else 0.0

        return 0.45 * struct_score + 0.35 * risk_score + 0.20 * exec_score

    def score_mutation_candidate(self, seed_code: str, infill_code: str, target_api: str, replace_type: str) -> float:
        state = self.extract_harness_state(seed_code, target_api)
        target_profile = self.metadata.get_risk_profile(target_api)
        target_cg = self.metadata.get_call_graph_entry(target_api)
        struct_score_map = {
            "argument": 0.55,
            "method": 0.65,
            "prefix": 0.72,
            "prefix-argument": 0.82,
            "suffix": 0.72,
            "suffix-argument": 0.82,
        }
        exec_score_map = {
            "argument": 0.95,
            "method": 0.7,
            "prefix": 0.52,
            "prefix-argument": 0.62,
            "suffix": 0.48,
            "suffix-argument": 0.58,
        }

        struct_score = struct_score_map.get(replace_type, 0.5)
        if state.unique_api_count <= 1 and replace_type in {"prefix", "suffix", "prefix-argument", "suffix-argument"}:
            struct_score += 0.15
        if state.call_chain_len <= 2 and replace_type in {"method", "prefix", "suffix"}:
            struct_score += 0.1

        risk_score = 0.0
        if target_profile is not None:
            if target_profile.boundary_hints and "argument" in replace_type:
                risk_score += 0.35
            if target_profile.high_risk_neighbors and replace_type in {"method", "prefix", "suffix", "prefix-argument", "suffix-argument"}:
                risk_score += 0.4
            risk_score += 0.25 * target_profile.risk_level
        if target_cg is not None:
            if target_cg.neighbors and replace_type in {"method", "prefix", "suffix"}:
                risk_score += 0.2
            if target_cg.cleanup_paths and replace_type in {"prefix", "suffix", "prefix-argument", "suffix-argument"}:
                risk_score += 0.08
            if not state.risk_path_hits and target_cg.short_call_chains and replace_type in {"method", "prefix", "suffix", "prefix-argument", "suffix-argument"}:
                risk_score += 0.32
            if not any(neighbor in state.called_apis for neighbor in target_cg.neighbors) and target_cg.neighbors and replace_type in {"method", "prefix", "suffix", "prefix-argument", "suffix-argument"}:
                risk_score += 0.18
            risk_score += 0.15 * target_cg.cg_priority

        exec_score = exec_score_map.get(replace_type, 0.5)
        if state.target_hit and replace_type == "method":
            exec_score -= 0.05
        if target_cg is not None and not target_cg.cleanup_paths and replace_type in {"prefix", "suffix"}:
            exec_score -= 0.08
        if infill_code.count("<|mask:") > 1:
            risk_score += 0.05
            exec_score -= 0.03

        return 0.35 * struct_score + 0.4 * risk_score + 0.25 * exec_score

    def build_feedback(
        self,
        code: str,
        target_api: str,
        status: ExecutionStatus,
        message: str,
        coverage_gain: bool = False,
    ) -> MutationFeedback:
        state = self.extract_harness_state(code, target_api)
        lower = message.lower()
        sanitizer_hits = []
        if "addresssanitizer" in lower or "heap-buffer-overflow" in lower:
            sanitizer_hits.append("asan")
        if "undefinedbehavior" in lower or "runtime error:" in lower:
            sanitizer_hits.append("ubsan")
        compile_ok = not message.startswith("CompileError")
        run_ok = status == ExecutionStatus.SUCCESS
        crash = status == ExecutionStatus.CRASH
        timeout = status == ExecutionStatus.TIMEOUT
        risk_gain = min(1.0, 0.18 * len(state.risk_hits) + 0.45 * len(state.risk_path_hits) + 0.08 * len(state.boundary_hint_hits))
        reward = (
            0.22 * float(compile_ok)
            + 0.22 * float(run_ok)
            + 0.18 * float(coverage_gain)
            + 0.18 * risk_gain
            + 0.20 * float(bool(sanitizer_hits))
        )
        if not state.target_hit:
            reward *= 0.35
        return MutationFeedback(
            compile_ok=compile_ok,
            run_ok=run_ok,
            coverage_gain=coverage_gain,
            target_hit=state.target_hit,
            risk_hits=state.risk_hits,
            risk_path_hits=state.risk_path_hits,
            boundary_hint_hits=state.boundary_hint_hits,
            sanitizer_hits=sanitizer_hits,
            crash=crash,
            timeout=timeout,
            reward=max(0.0, reward),
        )

    def reward_from_feedbacks(self, feedbacks: list[MutationFeedback]) -> float:
        if not feedbacks:
            return 0.0
        return sum(feedback.reward for feedback in feedbacks) / max(1, len(feedbacks))


def freshness_penalty(used_as_seed: int) -> float:
    return 0.12 * math.log1p(max(used_as_seed, 0))
