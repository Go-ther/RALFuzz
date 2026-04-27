from __future__ import annotations

import heapq
import math

import numpy as np

from ctitanfuzz.c_mutators import DepthFinder, SnippetInfill, SnippetInfillArbitraryAPI, UniqueFinder
from ctitanfuzz.guidance import MutationFeedback, RiskGuidance, freshness_penalty
from ctitanfuzz.metadata import stable_softmax


class GA:
    def __init__(
        self,
        initial_seeds,
        num_selection,
        num_generated,
        folder,
        api_call,
        mask_identifier,
        full_api_list,
        relaxargmut,
        seed_selection_algo="fitness",
        mutator_selection_algo="heuristic",
        use_single_mutator=False,
        replace_type=None,
        seed_pool_size=30,
        mutator_set="all",
        guidance: RiskGuidance | None = None,
    ):
        self.num_generated = num_generated
        self.num_selection = num_selection
        self.api_call = api_call
        self.mask_identifier = mask_identifier
        self.folder = folder
        self.full_api_list = full_api_list
        self.relaxargmut = relaxargmut
        self.seed_selection_algo = seed_selection_algo
        self.mutator_selection_algo = mutator_selection_algo
        self.use_single_mutator = use_single_mutator
        self.replace_type = replace_type
        self.seed_pool_size = seed_pool_size
        self.mutator_set = mutator_set
        self.guidance = guidance

        self.recursive_infill = SnippetInfillArbitraryAPI(mask_identifier, self.full_api_list)
        self.num_valid = 0
        self.behavior_signature_counts: dict[str, int] = {}

        self._init_seed(initial_seeds)
        self._init_mutator()

    def _extract_state(self, code: str):
        if self.guidance is None:
            return None
        return self.guidance.extract_harness_state(code, self.api_call)

    def _behavior_signature(self, code: str, state=None) -> str:
        if self.guidance is None:
            return ""
        if state is None:
            state = self._extract_state(code)
        return self.guidance.behavior_signature_from_state(state)

    def _novelty_bonus(self, signature: str) -> float:
        if not signature:
            return 0.0
        seen = self.behavior_signature_counts.get(signature, 0)
        return 0.45 / (1.0 + seen) - 0.18 * math.log1p(seen)

    def _signature_penalty(self, signature: str) -> float:
        if not signature:
            return 0.0
        seen = self.behavior_signature_counts.get(signature, 0)
        return 0.16 * math.log1p(max(seen - 1, 0))

    def _register_behavior_signature(self, signature: str) -> None:
        if not signature:
            return
        self.behavior_signature_counts[signature] = self.behavior_signature_counts.get(signature, 0) + 1

    def _behavior_fields(self, code: str, state=None) -> dict[str, object]:
        if self.guidance is None:
            return {
                "called_apis": [],
                "call_skeleton": [],
                "risk_hits": [],
                "risk_path_hits": [],
                "boundary_hint_hits": [],
                "construction_tags": [],
                "behavior_signature": "",
                "novelty_bonus": 0.0,
            }
        if state is None:
            state = self._extract_state(code)
        signature = self._behavior_signature(code, state)
        return {
            "called_apis": state.called_apis,
            "call_skeleton": state.call_skeleton,
            "risk_hits": state.risk_hits,
            "risk_path_hits": state.risk_path_hits,
            "boundary_hint_hits": state.boundary_hint_hits,
            "construction_tags": state.construction_tags,
            "behavior_signature": signature,
            "novelty_bonus": self._novelty_bonus(signature),
        }

    def _base_seed_info(self, seed: str, idx: int) -> dict[str, object]:
        state = self._extract_state(seed)
        behavior_fields = self._behavior_fields(seed, state)
        fitness_total = self._compute_fitness_score(
            seed,
            behavior_signature=str(behavior_fields["behavior_signature"]),
        )
        info = {
            "mutation_layer": 0,
            "used_as_seed": 0,
            "parent": None,
            "filename": "{}_seed{}.c".format(self.api_call, idx + 1),
            "fitness_total": fitness_total,
            **behavior_fields,
        }
        self._register_behavior_signature(str(behavior_fields["behavior_signature"]))
        return info

    def _extract_called_apis(self, code: str) -> list[str]:
        if self.guidance is None:
            return []
        return self.guidance.extract_harness_state(code, self.api_call).called_apis

    def _extract_risk_hits(self, code: str) -> list[str]:
        if self.guidance is None:
            return []
        return self.guidance.extract_harness_state(code, self.api_call).risk_hits

    def _extract_risk_paths(self, code: str) -> list[str]:
        if self.guidance is None:
            return []
        return self.guidance.extract_harness_state(code, self.api_call).risk_path_hits

    def _init_seed(self, initial_seeds):
        self.seeds = []
        self.info_code = {}
        for idx, seed in enumerate(initial_seeds):
            heapq.heappush(self.seeds, (-self.num_generated / 2, seed))
            self.info_code[seed] = self._base_seed_info(seed, idx)

    def _init_multi_arm(self):
        self.replace_type_p = {}
        for replace_type in self.replace_type:
            self.replace_type_p[replace_type] = [1.0, 2.0]
        self.epsilon = 0.1

    def _init_mutator(self):
        if self.use_single_mutator:
            if self.replace_type is None:
                raise ValueError("--replace_type is required when --use_single_mutator is set")
            self.replace_type = [self.replace_type]
        else:
            if self.mutator_set == "noprefix":
                self.replace_type = ["argument", "method", "suffix", "suffix-argument"]
            elif self.mutator_set == "nosuffix":
                self.replace_type = ["argument", "method", "prefix", "prefix-argument"]
            elif self.mutator_set == "noargument":
                self.replace_type = ["method", "prefix", "prefix-argument", "suffix", "suffix-argument"]
            elif self.mutator_set == "nomethod":
                self.replace_type = ["argument", "prefix", "prefix-argument", "suffix", "suffix-argument"]
            elif self.mutator_set == "all":
                self.replace_type = ["argument", "method", "prefix", "prefix-argument", "suffix", "suffix-argument"]
            else:
                raise ValueError("Replace_type {} not supported.".format(self.mutator_set))

        if self.mutator_selection_algo == "heuristic":
            self.replace_type_p = {replace_type: float(self.num_generated * 3) for replace_type in self.replace_type}
        elif self.mutator_selection_algo in ["epsgreedy", "ucb", "ts"]:
            self._init_multi_arm()
        elif self.mutator_selection_algo == "random":
            self.replace_type_p = {replace_type: 1.0 for replace_type in self.replace_type}
        else:
            raise ValueError("Unsupported mutator selection algorithm: {}".format(self.mutator_selection_algo))

    def _make_infill(self, code: str, replace_type: str) -> tuple[int, str]:
        if replace_type == "argument":
            infill = SnippetInfill(
                mask_identifier=self.mask_identifier,
                api_call=self.api_call,
                full_api_list=self.full_api_list,
                replace_type="argument",
            )
            num_replaced, infill_code, _ = infill.add_infill(code)
            if num_replaced == 0 and self.relaxargmut:
                num_replaced, infill_code, _ = self.recursive_infill.add_infill(code, replace_method=False)
            return num_replaced, infill_code
        if replace_type == "method" and (self.relaxargmut or replace_type == "method"):
            num_replaced, infill_code, _ = self.recursive_infill.add_infill(
                code,
                replace_method=(replace_type == "method"),
            )
            if num_replaced == 0:
                infill = SnippetInfill(
                    mask_identifier=self.mask_identifier,
                    api_call=self.api_call,
                    full_api_list=self.full_api_list,
                    replace_type="argument",
                )
                num_replaced, infill_code, _ = infill.add_infill(code)
            return num_replaced, infill_code
        infill = SnippetInfill(
            mask_identifier=self.mask_identifier,
            api_call=self.api_call,
            full_api_list=self.full_api_list,
            replace_type=replace_type,
        )
        num_replaced, infill_code, _ = infill.add_infill(code)
        return num_replaced, infill_code

    def _mutator_mean(self, replace_type: str) -> float:
        if self.mutator_selection_algo == "heuristic":
            total = sum(float(value) for value in self.replace_type_p.values()) or 1.0
            return float(self.replace_type_p[replace_type]) / total
        if self.mutator_selection_algo == "random":
            return 1.0 / max(1, len(self.replace_type))
        reward_total, trials = self.replace_type_p[replace_type]
        return float(reward_total) / max(float(trials), 1.0)

    def _sample_mutator_biases(self) -> dict[str, float]:
        if self.mutator_selection_algo == "heuristic":
            return {replace_type: self._mutator_mean(replace_type) for replace_type in self.replace_type}
        if self.mutator_selection_algo == "random":
            return {replace_type: 0.0 for replace_type in self.replace_type}
        if self.mutator_selection_algo == "epsgreedy":
            if np.random.uniform(0.0, 1.0) < self.epsilon:
                choice = np.random.choice(self.replace_type)
                return {replace_type: (1.0 if replace_type == choice else 0.0) for replace_type in self.replace_type}
            return {replace_type: self._mutator_mean(replace_type) for replace_type in self.replace_type}
        if self.mutator_selection_algo == "ucb":
            total_num = sum(value[1] for value in self.replace_type_p.values())
            log_t_2 = 2.0 * math.log(max(total_num, 2.0))
            biases = {}
            for replace_type, (reward_total, trials) in self.replace_type_p.items():
                mean = reward_total / max(trials, 1.0)
                biases[replace_type] = mean + math.sqrt(log_t_2 / max(trials, 1.0))
            return biases
        if self.mutator_selection_algo == "ts":
            biases = {}
            for replace_type, (reward_total, trials) in self.replace_type_p.items():
                alpha = 1.0 + reward_total
                beta = 1.0 + max(trials - reward_total, 0.0)
                biases[replace_type] = float(np.random.beta(alpha, beta))
            return biases
        raise NotImplementedError

    def _select_mutation_candidate(self, code: str):
        candidates = []
        biases = self._sample_mutator_biases()
        for replace_type in self.replace_type:
            num_replaced, infill_code = self._make_infill(code, replace_type)
            if num_replaced < 1 or not infill_code:
                continue
            guidance_score = (
                self.guidance.score_mutation_candidate(code, infill_code, self.api_call, replace_type)
                if self.guidance is not None
                else 0.0
            )
            combined_score = guidance_score + 0.3 * biases.get(replace_type, 0.0)
            candidates.append((combined_score, code, infill_code, replace_type))
        if not candidates:
            return None
        if self.mutator_selection_algo == "random":
            index = int(np.random.randint(0, len(candidates)))
            return candidates[index][1:]
        probs = stable_softmax([candidate[0] for candidate in candidates])
        index = int(np.random.choice(len(candidates), p=probs))
        return candidates[index][1:]

    def _add_new_seed(
        self,
        seed: str,
        code: str,
        replace_type: str,
        rd: int,
        filename: str,
        feedback: MutationFeedback | None = None,
    ):
        if code in self.info_code:
            return
        self.num_valid += 1
        state = self._extract_state(code)
        behavior_fields = self._behavior_fields(code, state)
        fitness_total = self._compute_fitness_score(
            code,
            feedback,
            behavior_signature=str(behavior_fields["behavior_signature"]),
        )
        self.info_code[code] = {
            "mutation_layer": self.info_code[seed]["mutation_layer"] + 1,
            "used_as_seed": 0,
            "parent": seed,
            "replace_type": replace_type,
            "round": rd,
            "filename": filename,
            "fitness_total": fitness_total,
            **behavior_fields,
        }
        self._register_behavior_signature(str(behavior_fields["behavior_signature"]))
        heapq.heappush(self.seeds, (-self.num_generated / 2, code))

    def _update_seed(self, code, value):
        if value == 0:
            value = -1
        heapq.heappush(self.seeds, (-value, code))
        self.info_code[code]["used_as_seed"] += self.num_generated

    def _select_seed(self):
        return heapq.heappop(self.seeds)[-1]

    def selection(self):
        selections = []
        while self.seeds and len(selections) != self.num_selection:
            code = self._select_seed()
            candidate = self._select_mutation_candidate(code)
            if candidate is None:
                continue
            selections.append(candidate)
        return selections

    def _reward_value(self, feedbacks: list[MutationFeedback]) -> float:
        if self.guidance is not None:
            return self.guidance.reward_from_feedbacks(feedbacks)
        if not feedbacks:
            return 0.0
        return sum(1.0 for feedback in feedbacks if feedback.run_ok) / max(1, len(feedbacks))

    def _update_mutator(self, feedbacks: list[MutationFeedback], replace_type: str):
        reward = self._reward_value(feedbacks)
        if self.mutator_selection_algo == "heuristic":
            compile_failures = sum(1 for feedback in feedbacks if not feedback.compile_ok)
            self.replace_type_p[replace_type] += reward * max(len(feedbacks), 1) - 0.25 * compile_failures
            self.replace_type_p[replace_type] = max(0.2, self.replace_type_p[replace_type])
        elif self.mutator_selection_algo in ["epsgreedy", "ucb", "ts"]:
            self.replace_type_p[replace_type][0] += reward
            self.replace_type_p[replace_type][1] += max(len(feedbacks), 1)
        elif self.mutator_selection_algo == "random":
            pass
        else:
            raise NotImplementedError

    def _compute_fitness_score(
        self,
        code: str,
        feedback: MutationFeedback | None = None,
        behavior_signature: str | None = None,
    ) -> float:
        max_depth = DepthFinder(self.full_api_list).max_depth(code)
        unique_calls, exact_repeats, _ = UniqueFinder(self.full_api_list).count(code)
        if self.guidance is not None:
            base_score = self.guidance.score_seed(code, self.api_call, feedback)
        else:
            base_score = unique_calls + max_depth - exact_repeats
        signature = behavior_signature if behavior_signature is not None else self._behavior_signature(code)
        return base_score + self._novelty_bonus(signature)

    def update(
        self,
        seed,
        generations,
        replace_type,
        rd,
        filenames,
        generation_feedbacks: list[MutationFeedback] | None = None,
        feedbacks: list[MutationFeedback] | None = None,
        add_flags: list[bool] | None = None,
    ):
        generation_feedbacks = generation_feedbacks or []
        feedbacks = feedbacks or generation_feedbacks
        self._update_seed(seed, len(generations))
        self._update_mutator(feedbacks, replace_type)
        for generation, filename, generation_feedback in zip(generations, filenames, generation_feedbacks):
            self._add_new_seed(seed, generation, replace_type, rd, filename, generation_feedback)

    def get_highest_order_output(self):
        highest_order = max([value["mutation_layer"] for value in self.info_code.values()])
        best_code = ""
        best_fitness = -float("inf")
        for code, value in self.info_code.items():
            if value["mutation_layer"] != highest_order:
                continue
            fitness = float(value.get("fitness_total", 0.0))
            if fitness > best_fitness:
                best_fitness = fitness
                best_code = code
        return best_code, highest_order

    def get_p(self):
        if self.mutator_selection_algo == "random":
            return [1.0 / len(self.replace_type)] * len(self.replace_type)
        if self.mutator_selection_algo == "heuristic":
            total = sum(float(value) for value in self.replace_type_p.values()) or 1.0
            return [float(self.replace_type_p[x]) / total for x in self.replace_type]
        return self.replace_type_p


class GA_Random(GA):
    def _init_seed(self, initial_seeds):
        self.seeds = []
        self.info_code = {}
        for idx, seed in enumerate(initial_seeds):
            self.seeds.append(seed)
            self.info_code[seed] = self._base_seed_info(seed, idx)

    def _update_seed(self, code, value):
        self.info_code[code]["used_as_seed"] += self.num_generated
        self.seeds.append(code)

    def _select_seed(self):
        code = np.random.choice(self.seeds)
        self.seeds.remove(code)
        return code

    def _add_new_seed(
        self,
        seed: str,
        code: str,
        replace_type: str,
        rd: int,
        filename: str,
        feedback: MutationFeedback | None = None,
    ):
        if code in self.info_code:
            return
        self.num_valid += 1
        state = self._extract_state(code)
        behavior_fields = self._behavior_fields(code, state)
        self.info_code[code] = {
            "mutation_layer": self.info_code[seed]["mutation_layer"] + 1,
            "used_as_seed": 0,
            "parent": seed,
            "replace_type": replace_type,
            "round": rd,
            "filename": filename,
            "fitness_total": self._compute_fitness_score(
                code,
                feedback,
                behavior_signature=str(behavior_fields["behavior_signature"]),
            ),
            **behavior_fields,
        }
        self._register_behavior_signature(str(behavior_fields["behavior_signature"]))
        self.seeds.append(code)


class GA_Coverage(GA_Random):
    def update(
        self,
        seed,
        generations,
        replace_type,
        rd,
        filenames,
        generation_feedbacks: list[MutationFeedback] | None = None,
        feedbacks: list[MutationFeedback] | None = None,
        add_flags: list[bool] | None = None,
    ):
        generation_feedbacks = generation_feedbacks or []
        feedbacks = feedbacks or generation_feedbacks
        add_flags = add_flags or [True] * len(generations)
        self._update_seed(seed, len(generations))
        self._update_mutator(feedbacks, replace_type)
        for generation, filename, generation_feedback, add_flag in zip(
            generations,
            filenames,
            generation_feedbacks,
            add_flags,
        ):
            if add_flag:
                self._add_new_seed(seed, generation, replace_type, rd, filename, generation_feedback)


class GAR_depth(GA):
    def _init_seed(self, initial_seeds):
        self.seeds = []
        self.info_code = {}
        for idx, seed in enumerate(initial_seeds):
            self.info_code[seed] = self._base_seed_info(seed, idx)
            heapq.heappush(
                self.seeds,
                (self.info_code[seed]["fitness_total"], -len(seed.splitlines()), seed),
            )
        if self.seed_pool_size > 0:
            while len(self.seeds) > self.seed_pool_size:
                heapq.heappop(self.seeds)

    def _add_new_seed(
        self,
        seed: str,
        code: str,
        replace_type: str,
        rd: int,
        filename: str,
        feedback: MutationFeedback | None = None,
    ):
        if code in self.info_code:
            return
        self.num_valid += 1
        state = self._extract_state(code)
        behavior_fields = self._behavior_fields(code, state)
        fitness_total = self._compute_fitness_score(
            code,
            feedback,
            behavior_signature=str(behavior_fields["behavior_signature"]),
        )
        self.info_code[code] = {
            "mutation_layer": self.info_code[seed]["mutation_layer"] + 1,
            "used_as_seed": 0,
            "parent": seed,
            "replace_type": replace_type,
            "round": rd,
            "filename": filename,
            "fitness_total": fitness_total,
            **behavior_fields,
        }
        self._register_behavior_signature(str(behavior_fields["behavior_signature"]))
        heapq.heappush(
            self.seeds,
            (fitness_total, -len(code.splitlines()), code),
        )
        if self.seed_pool_size > 0:
            while len(self.seeds) > self.seed_pool_size:
                heapq.heappop(self.seeds)

    def _update_seed(self, code, value):
        self.info_code[code]["used_as_seed"] += self.num_generated

    def _select_seed(self):
        codes = [record[-1] for record in self.seeds]
        scores = []
        for code in codes:
            info = self.info_code[code]
            dynamic_score = float(info.get("fitness_total", 0.0))
            dynamic_score += 0.06 * float(info.get("mutation_layer", 0))
            dynamic_score -= freshness_penalty(int(info.get("used_as_seed", 0)))
            dynamic_score -= self._signature_penalty(str(info.get("behavior_signature", "")))
            scores.append(dynamic_score)
        probs = stable_softmax(scores)
        return np.random.choice(codes, p=probs)
