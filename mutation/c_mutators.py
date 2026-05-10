from __future__ import annotations

import random
import re
from dataclasses import dataclass

from mutation.util.util import normalize_code


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


def find_brace_blocks(snippet: str) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    stack: list[int] = []
    line_no = 0
    index = 0
    in_string: str | None = None
    line_comment = False
    block_comment = False
    escape = False

    while index < len(snippet):
        char = snippet[index]
        nxt = snippet[index + 1] if index + 1 < len(snippet) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
                line_no += 1
            index += 1
            continue

        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
                continue
            if char == "\n":
                line_no += 1
            index += 1
            continue

        if in_string is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            if char == "\n":
                line_no += 1
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

        if char == "{":
            stack.append(line_no)
        elif char == "}":
            if stack:
                blocks.append((stack.pop(), line_no))
        elif char == "\n":
            line_no += 1
        index += 1

    return blocks


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

    def _candidate_block_windows(self, code: str, line_no: int) -> list[tuple[int, int]]:
        candidates: list[tuple[int, int]] = []
        for open_line, close_line in find_brace_blocks(code):
            if open_line <= line_no <= close_line:
                start_line = open_line + 1
                end_line = close_line
                if start_line <= end_line:
                    candidates.append((start_line, end_line))
        candidates.sort(key=lambda item: (item[1] - item[0], item[0]))
        return candidates

    def _pick_prefix_window(self, code: str, line_no: int) -> tuple[int, int]:
        for start_line, end_line in self._candidate_block_windows(code, line_no):
            insert_min = start_line
            insert_max = min(line_no, end_line)
            if insert_min > insert_max:
                continue
            end_replace = random.randint(insert_min, insert_max)
            start_replace = random.randint(insert_min, end_replace)
            return start_replace, end_replace
        fallback = max(0, line_no)
        return fallback, fallback

    def _pick_suffix_window(self, code: str, line_no: int) -> tuple[int, int]:
        for start_line, end_line in self._candidate_block_windows(code, line_no):
            insert_min = min(max(line_no + 1, start_line), end_line)
            insert_max = end_line
            if insert_min > insert_max:
                continue
            start_replace = random.randint(insert_min, insert_max)
            end_replace = random.randint(start_replace, insert_max)
            return start_replace, end_replace
        fallback = max(0, line_no + 1)
        return fallback, fallback

    def _replace_line_window(
        self,
        code: str,
        start_replace: int,
        end_replace: int,
        mask_idx: int = 0,
    ) -> str:
        lines = code.splitlines()
        if len(lines) == 0:
            return self._mask(mask_idx)
        start_replace = min(max(start_replace, 0), len(lines))
        end_replace = min(max(end_replace, start_replace), len(lines))
        output = "\n".join(lines[:start_replace])
        if output:
            output += "\n"
        output += self._mask(mask_idx)
        if end_replace < len(lines):
            output += "\n" + "\n".join(lines[end_replace:])
        return output

    def _replace_argument(self, code: str, span: CallSpan, mask_idx: int = 0) -> str:
        return code[: span.paren_start + 1] + self._mask(mask_idx) + code[span.paren_end :]

    def _replace_method(self, code: str, span: CallSpan, mask_idx: int = 0) -> str:
        return (
            code[: span.name_start]
            + self._mask(mask_idx)
            + code[span.name_start + len(span.api_name) :]
        )

    def _replace_neighbor(self, code: str, span: CallSpan, mask_idx: int = 0) -> str:
        lines = code.splitlines()
        if len(lines) == 0 or span.line_no >= len(lines):
            return self._mask(mask_idx)

        target_line = lines[span.line_no]
        indent_match = re.match(r"\s*", target_line)
        indent = indent_match.group(0) if indent_match else ""

        result_var = None
        assign_pattern = re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*{}\s*\(".format(re.escape(span.api_name))
        )
        assign_match = assign_pattern.search(target_line)
        if assign_match:
            result_var = assign_match.group(1)

        arg_text = code[span.paren_start + 1 : span.paren_end].strip()
        condition_parts: list[str] = []
        if result_var:
            condition_parts.append("{} == NULL".format(result_var))
        if arg_text and "," not in arg_text and arg_text != "NULL":
            condition_parts.append("{} != NULL".format(arg_text))
        condition = " && ".join(condition_parts) if condition_parts else "1"

        insertion = [
            "{}if ({}) {{".format(indent, condition),
            "{}    {}".format(indent, self._mask(mask_idx)),
            "{}}}".format(indent),
        ]
        output_lines = lines[: span.line_no + 1] + insertion + lines[span.line_no + 1 :]
        return "\n".join(output_lines)

    def _replace_prefix(self, code: str, line_no: int, mask_idx: int = 0) -> str:
        start_replace, end_replace = self._pick_prefix_window(code, line_no)
        return self._replace_line_window(code, start_replace, end_replace, mask_idx)

    def _replace_suffix(self, code: str, line_no: int, mask_idx: int = 0) -> str:
        start_replace, end_replace = self._pick_suffix_window(code, line_no)
        return self._replace_line_window(code, start_replace, end_replace, mask_idx)

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
        elif self.replace_type == "neighbor":
            infill_code = self._replace_neighbor(original_code, span, 0)
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
