from __future__ import annotations

import random
import re

from mutation.llm.base import BaseLLM, MaskedCodeMixin


class MockInfillLLM(BaseLLM, MaskedCodeMixin):
    def __init__(self, target_adapter) -> None:
        super().__init__(infill_ph="<|mask:{}|>")
        self.target_adapter = target_adapter
        self.function_bank = target_adapter.get_mock_function_bank()
        self.statement_bank = target_adapter.get_mock_statement_bank()
        self.argument_bank = target_adapter.get_mock_argument_bank()
        self.allowed_tokens = {
            "NULL",
            "strlen",
            "sizeof",
            "if",
            "return",
            "const",
            "char",
            "unsigned",
            "int",
            "size_t",
            "input_text",
            "input_length",
            "mutable_buffer",
            "parse_end",
            "result",
            "root",
            "item",
            "item_obj",
            "ctx",
            "rendered",
        }
        metadata = getattr(target_adapter, "metadata", None)
        self.api_arity = {
            api_name: len(spec.arg_types)
            for api_name, spec in getattr(metadata, "api_specs", {}).items()
        }

    def _mask_position(self, infill_code: str, idx: int) -> int:
        return infill_code.find("<|mask:{}|>".format(idx))

    def _infer_context_api(self, infill_code: str, idx: int) -> str | None:
        pos = self._mask_position(infill_code, idx)
        if pos < 0:
            return None
        prefix = infill_code[:pos]
        matches = []
        for api in self.function_bank:
            for match in re.finditer(r"\b{}\s*\(".format(re.escape(api)), prefix):
                matches.append((match.start(), api))
        if not matches:
            return None
        matches.sort()
        return matches[-1][1]

    def _infer_replacement(self, infill_code: str, idx: int) -> str:
        marker = "<|mask:{}|>".format(idx)
        pos = self._mask_position(infill_code, idx)
        if pos < 0:
            return ""
        around = infill_code[max(0, pos - 160) : pos + 160]
        api = self._infer_context_api(infill_code, idx)
        if marker + "(" in around:
            function_bank = self.function_bank
            if api is not None and api in self.api_arity:
                function_bank = [
                    candidate
                    for candidate in self.function_bank
                    if self.api_arity.get(candidate) == self.api_arity[api]
                ] or self.function_bank
            return random.choice(function_bank)
        if self._is_statement_mask(infill_code, idx):
            api = None
        if api is not None and api in self.argument_bank:
            candidates = []
            for candidate in self.argument_bank[api]:
                if not self._candidate_compatible(candidate, infill_code):
                    continue
                candidates.append(candidate)
            if candidates:
                return random.choice(candidates)
        statements = []
        for statement in self.statement_bank:
            if not self._candidate_compatible(statement, infill_code):
                continue
            statements.append(statement)
        if not statements:
            statements = ["/* mock no-op */"]
        return random.choice(statements)

    def _is_statement_mask(self, infill_code: str, idx: int) -> bool:
        pos = self._mask_position(infill_code, idx)
        if pos < 0:
            return False
        left = infill_code[:pos].rstrip()
        right = infill_code[pos + len("<|mask:{}|>".format(idx)) :].lstrip()
        prev_char = left[-1] if left else "\n"
        next_char = right[0] if right else "\n"
        return prev_char in {"\n", ";", "{", "}"} and next_char not in {"(", ","}

    def _candidate_compatible(self, candidate: str, infill_code: str) -> bool:
        for token in re.findall(r"[A-Za-z_]\w*", candidate):
            if token in self.allowed_tokens:
                continue
            if token in self.function_bank:
                continue
            if re.search(r"\b{}\b".format(re.escape(token)), infill_code):
                continue
            return False
        return True

    def model_predict_multi(self, infill_code: str, do_sample=False, num_samples=1000):
        expected_masks = self.get_mask_count(infill_code)
        if expected_masks == 0:
            return False, True, []
        outputs = []
        for _ in range(num_samples):
            replacements = []
            partially_filled = infill_code
            for idx in range(expected_masks):
                replacement = self._infer_replacement(partially_filled, idx)
                replacements.append(replacement)
                partially_filled = partially_filled.replace(
                    "<|mask:{}|>".format(idx), replacement
                )
            outputs.append(self.apply_replacements(infill_code, replacements))
        return len(outputs) > 0, True, outputs
