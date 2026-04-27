from __future__ import annotations

import random
import re
from dataclasses import dataclass

from ctitanfuzz.util.util import normalize_code


@dataclass
class CallSpan:
    api_name: str
    name_start: int
    paren_start: int
    paren_end: int
    line_no: int


def find_matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    index = open_idx
    in_string: str | None = None
    line_comment = False
    block_comment = False
    escape = False
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue

        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_string is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            index += 1
            continue

        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in ('"', "'"):
            in_string = char
            index += 1
            continue

        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def find_call_spans(snippet: str, api_names: list[str]) -> list[CallSpan]:
    spans: list[CallSpan] = []
    for api_name in sorted(api_names, key=len, reverse=True):
        pattern = re.compile(r"\b{}\s*\(".format(re.escape(api_name)))
        for match in pattern.finditer(snippet):
            open_idx = snippet.find("(", match.start())
            if open_idx < 0:
                continue
            close_idx = find_matching_paren(snippet, open_idx)
            if close_idx < 0:
                continue
            line_no = snippet.count("\n", 0, match.start())
            spans.append(
                CallSpan(
                    api_name=api_name,
                    name_start=match.start(),
                    paren_start=open_idx,
                    paren_end=close_idx,
                    line_no=line_no,
                )
            )
    spans.sort(key=lambda span: span.name_start)
    return spans


class UniqueFinder:
    def __init__(self, api_names: list[str]):
        self.api_names = api_names

    def count(self, snippet: str) -> tuple[int, int, int]:
        spans = find_call_spans(snippet, self.api_names)
        unique_found_apis: dict[str, int] = {}
        unique_found_call_exps: dict[str, int] = {}
        for span in spans:
            call_exp = re.sub(
                r"\s+",
                " ",
                snippet[span.name_start : span.paren_end + 1],
            ).strip()
            unique_found_call_exps[call_exp] = unique_found_call_exps.get(call_exp, 0) + 1
            unique_found_apis[span.api_name] = unique_found_apis.get(span.api_name, 0) + 1
        return (
            len(unique_found_apis),
            sum(max(value - 1, 0) for value in unique_found_call_exps.values()),
            sum(max(value - 1, 0) for value in unique_found_apis.values()),
        )


class DepthFinder:
    def __init__(self, api_names: list[str]):
        escaped = "|".join(re.escape(name) for name in api_names)
        self.api_regex = re.compile(r"\b({})\s*\(".format(escaped))

    def max_depth(self, snippet: str) -> int:
        depth = 0
        max_depth = 0
        for raw_line in snippet.splitlines():
            line = raw_line.split("//", 1)[0]
            if self.api_regex.search(line):
                max_depth = max(max_depth, depth + 1)
            depth += line.count("{")
            depth -= line.count("}")
            depth = max(depth, 0)
        return max_depth


class SnippetInfill:
    def __init__(
        self,
        mask_identifier: str,
        api_call: str,
        full_api_list: list[str],
        replace_type: str = "argument",
    ):
        self.mask_identifier = mask_identifier
        self.api_call = api_call
        self.full_api_list = full_api_list
        self.replace_type = replace_type

    def _mask(self, idx: int) -> str:
        return self.mask_identifier.format(idx)

    def _replace_argument(self, code: str, span: CallSpan, mask_idx: int = 0) -> str:
        return code[: span.paren_start + 1] + self._mask(mask_idx) + code[span.paren_end :]

    def _replace_method(self, code: str, span: CallSpan, mask_idx: int = 0) -> str:
        return (
            code[: span.name_start]
            + self._mask(mask_idx)
            + code[span.name_start + len(span.api_name) :]
        )

    def _replace_prefix(self, code: str, line_no: int, mask_idx: int = 0) -> str:
        lines = code.splitlines()
        if len(lines) == 0:
            return self._mask(mask_idx)
        end_replace = 0 if line_no <= 0 else random.randint(0, line_no - 1)
        start_replace = 0 if end_replace <= 0 else random.randint(0, end_replace)
        prefix_lines = lines[:start_replace]
        suffix_lines = lines[end_replace:]
        output = "\n".join(prefix_lines)
        if output:
            output += "\n"
        output += self._mask(mask_idx)
        if suffix_lines:
            output += "\n" + "\n".join(suffix_lines)
        return output

    def _replace_suffix(self, code: str, line_no: int, mask_idx: int = 0) -> str:
        lines = code.splitlines()
        if len(lines) == 0:
            return self._mask(mask_idx)
        line_no = min(max(line_no, 0), len(lines) - 1)
        start_replace = random.randint(line_no, len(lines) - 1)
        end_replace = random.randint(start_replace, len(lines))
        output = "\n".join(lines[:start_replace])
        if output:
            output += "\n"
        output += self._mask(mask_idx)
        if end_replace < len(lines):
            output += "\n" + "\n".join(lines[end_replace:])
        return output

    def add_infill(self, snippet: str) -> tuple[int, str, str]:
        original_code = normalize_code(snippet)
        spans = [
            span
            for span in find_call_spans(original_code, [self.api_call])
            if span.api_name == self.api_call
        ]
        if len(spans) == 0:
            return 0, "", original_code
        span = spans[0]
        if self.replace_type == "argument":
            infill_code = self._replace_argument(original_code, span, 0)
        elif self.replace_type == "method":
            infill_code = self._replace_method(original_code, span, 0)
        elif self.replace_type == "prefix":
            infill_code = self._replace_prefix(original_code, span.line_no, 0)
        elif self.replace_type == "suffix":
            infill_code = self._replace_suffix(original_code, span.line_no, 0)
        elif self.replace_type == "prefix-argument":
            arg_code = self._replace_argument(original_code, span, 1)
            infill_code = self._replace_prefix(arg_code, span.line_no, 0)
        elif self.replace_type == "suffix-argument":
            arg_code = self._replace_argument(original_code, span, 0)
            infill_code = self._replace_suffix(arg_code, span.line_no, 1)
        else:
            raise ValueError("Unsupported replace_type: {}".format(self.replace_type))
        return len(spans), infill_code, original_code


class SnippetInfillArbitraryAPI:
    def __init__(self, mask_identifier: str, full_api_list: list[str]):
        self.mask_identifier = mask_identifier
        self.full_api_list = full_api_list

    def add_infill(
        self,
        snippet: str,
        replace_method: bool = False,
    ) -> tuple[int, str, str]:
        original_code = normalize_code(snippet)
        spans = find_call_spans(original_code, self.full_api_list)
        if len(spans) == 0:
            return 0, "", original_code
        span = random.choice(spans)
        infill = SnippetInfill(
            mask_identifier=self.mask_identifier,
            api_call=span.api_name,
            full_api_list=self.full_api_list,
            replace_type="method" if replace_method else "argument",
        )
        return infill.add_infill(original_code)
