from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import List


class BaseLLM(ABC):
    def __init__(self, infill_ph: str):
        self.infill_ph = infill_ph

    @abstractmethod
    def model_predict_multi(
        self, infill_code: str, do_sample: bool = False, num_samples: int = 1000
    ):
        raise NotImplementedError


class MaskedCodeMixin:
    mask_pattern = re.compile(r"<\|mask:(\d+)\|>")

    def extract_mask_ids(self, infill_code: str) -> List[int]:
        return [int(mask_id) for mask_id in self.mask_pattern.findall(infill_code)]

    def get_mask_count(self, infill_code: str) -> int:
        mask_ids = self.extract_mask_ids(infill_code)
        if len(mask_ids) == 0:
            return 0
        return max(mask_ids) + 1

    def apply_replacements(self, infill_code: str, replacements: List[str]) -> str:
        output = infill_code
        for idx, replacement in enumerate(replacements):
            output = output.replace("<|mask:{}|>".format(idx), replacement)
        return output

    def parse_json_list(self, text: str, expected_count: int) -> List[str]:
        if expected_count == 0:
            return []

        candidates = [text.strip()]
        if "```json" in text:
            candidates.extend(re.findall(r"```json\s*(.*?)\s*```", text, re.S))
        if "```" in text:
            candidates.extend(re.findall(r"```\s*(.*?)\s*```", text, re.S))
        candidates.extend(re.findall(r"(\[[\s\S]*\])", text, re.S))
        if "{" in text and "}" in text:
            candidates.extend(re.findall(r"(\{[\s\S]*\})", text, re.S))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                for key in ("replacements", "masks", "output", "outputs"):
                    value = parsed.get(key)
                    if (
                        isinstance(value, list)
                        and len(value) == expected_count
                        and all(isinstance(item, str) for item in value)
                    ):
                        return value
                mask_values = []
                for idx in range(expected_count):
                    value = parsed.get(f"mask_{idx}", parsed.get(str(idx)))
                    if not isinstance(value, str):
                        break
                    mask_values.append(value)
                if len(mask_values) == expected_count:
                    return mask_values
            if (
                isinstance(parsed, list)
                and len(parsed) == expected_count
                and all(isinstance(item, str) for item in parsed)
            ):
                return parsed
        if expected_count == 1:
            fallback = text.strip()
            fenced = re.findall(r"```(?:c|C)?\s*(.*?)\s*```", fallback, re.S)
            if fenced:
                fallback = fenced[0].strip()
            return [fallback]
        raise ValueError("Unable to parse model response as a JSON string list.")
