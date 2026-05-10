from __future__ import annotations

import re
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, T5ForConditionalGeneration

from mutation.llm.base import BaseLLM


class LocalHuggingFaceLLM(BaseLLM):
    def __init__(self, pretrained: str = "", weight=None, batch_size=1):
        print("Initializing a SpanLM based model: {} ...".format(pretrained))
        t_start = time.time()
        self.pretrained = pretrained
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.extra_end = None
        if "Salesforce" in pretrained:
            self.model = T5ForConditionalGeneration.from_pretrained(pretrained)
            self.max_length = self.model.config.to_dict()["n_positions"]
            infill_ph = "<extra_id_0>"
        elif "facebook" in pretrained:
            if weight == "float16":
                self.model = AutoModelForCausalLM.from_pretrained(
                    pretrained, revision="float16", torch_dtype=torch.float16
                )
                self.model = self.model.half()
            else:
                self.model = AutoModelForCausalLM.from_pretrained(pretrained)
            self.max_length = self.model.config.to_dict()["max_position_embeddings"]
            infill_ph = "<|mask:{}|>"
            self.infill_pattern = re.compile(r"<\|mask:\d\|>")
            self.extra_end = "<|mask:1|><|mask:0|>"
            self.EOM = "<|endofmask|>"
            self.BOS = "<|endoftext|>"
            self.META_FILE = "<|/ file"
        else:
            raise NotImplementedError

        super().__init__(infill_ph=infill_ph)
        print("Max length: {}".format(self.max_length))
        self.model = self.model.to(self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(pretrained)
        self.tokenizer.pad_token = 0
        self.tokenizer.padding_side = "left"
        self.batch_size = batch_size
        print("Batch size: {}".format(batch_size))
        print("Model loading time: {}".format(time.time() - t_start))

    def build_input(self, infill_code: str):
        if self.extra_end:
            return infill_code + self.extra_end
        return infill_code

    def build_input_multi(self, infill_code: str, index: int, extra_end: int = 0):
        if extra_end != 0:
            return infill_code + "<|mask:{}|><|mask:{}|>".format(extra_end, index)
        return infill_code + "<|mask:{}|>".format(index)

    def model_predict_multi(self, infill_code: str, do_sample=False, num_samples=1000):
        parts = re.split(self.infill_pattern, infill_code)
        outputs, tmp_prompt = [], []

        for index, part in enumerate(parts[:-1]):
            if index == 0:
                n_infill_code = self.build_input_multi(
                    infill_code, index, len(parts) - 1
                )
                input_tokens = self.tokenizer.encode(
                    n_infill_code, return_tensors="pt"
                ).repeat(min(num_samples, self.batch_size), 1)
                input_tokens = input_tokens.to(self.device)
                with torch.no_grad():
                    raw_o = self.model.generate(
                        input_tokens,
                        max_length=len(input_tokens[0]) + 64,
                        do_sample=do_sample,
                        top_p=0.95,
                        temperature=1,
                    )
                    o = self.tokenizer.batch_decode(
                        raw_o, clean_up_tokenization_spaces=False
                    )
                    for output in o:
                        if output.startswith(self.BOS):
                            output = output[len(self.BOS) :]
                        output = output[
                            output.index(
                                "<|mask:{}|>".format(index),
                                output.index("<|mask:{}|>".format(index)) + 1,
                            )
                            + len("<|mask:{}|>".format(index)) :
                        ]
                        if self.EOM not in output:
                            continue
                        output = output[: output.index(self.EOM)]
                        if self.META_FILE in output:
                            output = output[: output.index(self.META_FILE)]
                        outputs.append(part + output)
                        tmp_prompt.append(n_infill_code + output + self.EOM)
            else:
                tmp_prompt = [self.build_input_multi(x, index) for x in tmp_prompt]
                if len(tmp_prompt) == 0:
                    return False, True, []
                input_tokens = self.tokenizer(
                    tmp_prompt, return_tensors="pt", padding="longest"
                ).input_ids
                input_tokens = input_tokens.to(self.device)
                with torch.no_grad():
                    raw_o = self.model.generate(
                        input_tokens,
                        max_length=len(input_tokens[0]) + 64,
                        do_sample=do_sample,
                        top_p=0.95,
                        temperature=1,
                    )
                    o = self.tokenizer.batch_decode(
                        raw_o, clean_up_tokenization_spaces=False
                    )
                    t_outputs = []
                    t_prompt = []
                    for i, output in enumerate(o):
                        if output.startswith(self.BOS):
                            output = output[len(self.BOS) :]
                        output = output[
                            output.index(
                                "<|mask:{}|>".format(index),
                                output.index("<|mask:{}|>".format(index)) + 1,
                            )
                            + len("<|mask:{}|>".format(index)) :
                        ]
                        if self.EOM not in output:
                            continue
                        output = output[: output.index(self.EOM)]
                        if self.META_FILE in output:
                            output = output[: output.index(self.META_FILE)]
                        t_outputs.append(outputs[i] + part + output)
                        t_prompt.append(tmp_prompt[i] + output + self.EOM)
                    outputs = t_outputs
                    tmp_prompt = t_prompt

        outputs = [x + parts[-1] for x in outputs]
        return len(outputs) > 0, True, outputs

