from __future__ import annotations

import json
import os
import time
from typing import Dict, List
from urllib import error, request

from ctitanfuzz.llm.base import BaseLLM, MaskedCodeMixin


def _usage_from_response(data: Dict) -> Dict:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return {
            "available": False,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
    return {
        "available": any(v is not None for v in (prompt_tokens, completion_tokens, total_tokens)),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _merge_usage(items: List[Dict]) -> Dict:
    available = [item for item in items if isinstance(item, dict) and item.get("available")]
    if not available:
        return {
            "available": False,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
    out = {"available": True}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        vals = [item.get(key) for item in available if item.get(key) is not None]
        out[key] = sum(int(v) for v in vals) if vals else 0
    return out


class OpenAICompatibleInfillLLM(BaseLLM, MaskedCodeMixin):
    def __init__(
        self,
        model_name: str,
        api_base: str,
        api_key: str,
        timeout: int = 60,
        max_tokens: int = 256,
        temperature: float = 1.0,
    ):
        super().__init__(infill_ph="<|mask:{}|>")
        self.model_name = model_name
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.usage_records: List[Dict] = []

    def _build_messages(
        self,
        infill_code: str,
        expected_masks: int,
        *,
        sample_index: int | None = None,
        total_samples: int | None = None,
    ) -> List[Dict[str, str]]:
        system_prompt = (
            "You are filling masked regions inside a C program used for library API fuzzing. "
            "Preserve the step-to-step guidance comments and return only valid JSON. "
            "Use this exact schema: {\"replacements\": [\"...\"]}. "
            "The replacements array length must equal the number of masks. "
            "Each string is the exact replacement text for the corresponding mask index. "
            "Do not include markdown, explanations, or the full program."
        )
        diversity_note = ""
        if sample_index is not None and total_samples is not None and total_samples > 1:
            diversity_note = (
                "\nThis is sample {}/{} for the same masked program. "
                "Keep it behaviorally distinct from sibling samples when possible by varying boundary cases, "
                "helper-call structure, local buffers, or cleanup choices, while preserving the target API."
            ).format(sample_index + 1, total_samples)
        user_prompt = (
            "Fill every mask in the following C code.\n"
            "Masks are named <|mask:0|>, <|mask:1|>, ... and the response must be a JSON object with a replacements array.\n"
            "Keep includes, ownership, cleanup, and target API usage consistent.\n"
            "Number of masks: {}{}\n\n"
            "{}"
        ).format(expected_masks, diversity_note, infill_code)
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _post_chat_completion(self, payload: Dict) -> tuple[str, Dict]:
        req = request.Request(
            self.api_base + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(self.api_key),
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError("LLM API HTTP {}: {}".format(exc.code, body))
        except error.URLError as exc:
            raise RuntimeError("LLM API request failed: {}".format(exc))

        choices = data.get("choices", [])
        if len(choices) == 0:
            raise RuntimeError("LLM API returned no choices: {}".format(data))
        usage = _usage_from_response(data)
        self.usage_records.append(usage)
        return choices[0]["message"]["content"], usage

    def get_usage_summary(self) -> Dict:
        return _merge_usage(self.usage_records)

    def _sample_once(
        self,
        infill_code: str,
        do_sample: bool,
        expected_masks: int,
        *,
        sample_index: int | None = None,
        total_samples: int | None = None,
    ) -> str:
        payload = {
            "model": self.model_name,
            "messages": self._build_messages(
                infill_code,
                expected_masks,
                sample_index=sample_index,
                total_samples=total_samples,
            ),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature if do_sample else 0.0,
            "top_p": 0.9,
            "think": False,
            "response_format": {"type": "json_object"},
        }
        content, _usage = self._post_chat_completion(payload)
        replacements = self.parse_json_list(content, expected_masks)
        return self.apply_replacements(infill_code, replacements)

    def model_predict_multi(self, infill_code: str, do_sample=False, num_samples=1000):
        expected_masks = self.get_mask_count(infill_code)
        if expected_masks == 0:
            return False, True, []

        outputs = []
        for sample_index in range(num_samples):
            try:
                outputs.append(
                    self._sample_once(
                        infill_code,
                        do_sample,
                        expected_masks,
                        sample_index=sample_index,
                        total_samples=num_samples,
                    )
                )
            except Exception as exc:
                print("Remote LLM generation failed: {}".format(exc))
                time.sleep(0.2)
                continue
        return len(outputs) > 0, True, outputs


class DeepSeekInfillLLM(OpenAICompatibleInfillLLM):
    def __init__(
        self,
        model_name: str = "deepseek-chat",
        api_base: str = "https://api.deepseek.com",
        api_key: str | None = None,
        timeout: int = 60,
        max_tokens: int = 256,
        temperature: float = 1.0,
    ):
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DeepSeek API key is missing. Set --llm_api_key or DEEPSEEK_API_KEY.")
        super().__init__(
            model_name=model_name,
            api_base=api_base,
            api_key=api_key,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
        )
