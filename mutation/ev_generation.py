from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutation.c_mutators import SnippetInfill
from mutation.coverage import CoverageTracker
from mutation.guidance import MutationFeedback, RiskGuidance
from mutation.llm import create_llm_client
from mutation.process_file import clean_code, get_initial_programs
from mutation.seed_pool import GA_Coverage, GA_Random, GAR_depth
from mutation.targets import create_target_adapter
from mutation.toolchain_env import configure_clang_environment, default_coverage_tool, normalize_clang_compiler
from mutation.util.logger import Logger
from mutation.util.util import ExecutionStatus, load_apis, set_seed
from mutation.validate import validate_testcase


def _resolve_build_root() -> Path:
    override = os.environ.get("RALFUZZ_BUILD_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parent / ".build"


BUILD_ROOT = _resolve_build_root()


def _fallback_feedback(report, target_hit: bool, coverage_gain: bool = False) -> MutationFeedback:
    reward = 0.25 * float(report.compile_ok) + 0.25 * float(report.run_ok) + 0.15 * float(coverage_gain)
    if not target_hit:
        reward *= 0.35
    return MutationFeedback(
        compile_ok=report.compile_ok,
        run_ok=report.run_ok,
        coverage_gain=coverage_gain,
        target_hit=target_hit,
        crash=report.crash,
        timeout=report.timeout,
        sanitizer_hits=report.sanitizer_hits,
        reward=reward,
    )


def generate_loop(args, model, target_adapter, guidance, full_api_list, original_codes: list[str], api: str, logger: Logger, max_valid: int):
    num_selection = max(1, int(args.num_selection))
    num_valid = 0
    num_generated = 0
    generation_time: list[float] = []
    validation_time: list[float] = []
    total_run_time: list[float] = []
    num_timeout = 0
    num_exception = 0
    num_crash = 0
    num_duplicated = 0
    num_notarget = 0
    total_outputs = set(original_codes)

    ga_class = GAR_depth
    if args.seed_selection_algo == "random":
        ga_class = GA_Random
    elif args.seed_selection_algo == "coverage":
        ga_class = GA_Coverage

    ga = ga_class(
        original_codes,
        num_selection,
        args.batch_size,
        args.folder,
        api,
        model.infill_ph,
        full_api_list,
        args.relaxargmut,
        args.seed_selection_algo,
        args.mutator_selection_algo,
        args.use_single_mutator,
        args.replace_type,
        args.seed_pool_size,
        args.mutator_set,
        guidance=guidance,
    )
    coverage_tracker = None
    if args.seed_selection_algo == "coverage":
        coverage_tracker = CoverageTracker(
            target_adapter=target_adapter,
            target_root=args.target_root,
            compiler=args.compiler,
            coverage_tool=args.coverage_tool,
            build_root=BUILD_ROOT,
            compile_timeout=args.compile_timeout,
            test_timeout=args.test_timeout,
        )

    crashes = []
    round_id = 0
    stagnation_rounds = 0
    while (max_valid < 0 or num_valid < max_valid) and sum(total_run_time) < args.timeout and (
        int(getattr(args, "max_rounds", 0) or 0) <= 0 or round_id < int(args.max_rounds)
    ):
        logger.logo("--- Round : {} ---".format(round_id))
        start_time_total = time.time()
        round_valid = 0
        selections = ga.selection()
        if len(selections) == 0:
            logger.logo("--- No selectable seeds remain, stop generation ---")
            break

        round_generation = 0.0
        round_validation = 0.0
        for seed, infill_code, replace_type in selections:
            generations = []
            filenames = []
            add_flags = []
            generation_feedbacks: list[MutationFeedback] = []
            all_feedbacks: list[MutationFeedback] = []

            prompt_code = target_adapter.attach_generation_context(api, infill_code)
            start = time.time()
            _, _, outputs = model.model_predict_multi(
                prompt_code,
                do_sample=True,
                num_samples=args.batch_size,
            )
            round_generation += time.time() - start

            for output in outputs:
                output = clean_code(output, target_adapter)
                num_generated += 1

                if output in total_outputs:
                    num_duplicated += 1
                    continue
                total_outputs.add(output)

                num_replaced, _, _ = SnippetInfill(
                    mask_identifier=model.infill_ph,
                    api_call=api,
                    full_api_list=full_api_list,
                    replace_type="argument",
                ).add_infill(output)

                start = time.time()
                report = validate_testcase(
                    output,
                    target_adapter=target_adapter,
                    target_root=args.target_root,
                    compiler=args.compiler,
                    build_root=BUILD_ROOT,
                    compile_timeout=args.compile_timeout,
                    test_timeout=args.test_timeout,
                    enable_sanitizer=args.enable_sanitizer,
                )
                round_validation += time.time() - start
                target_hit = True
                if guidance is not None:
                    target_hit = guidance.extract_harness_state(output, api).target_hit

                coverage_gain = False
                if report.run_ok and coverage_tracker is not None:
                    status_cov, new_coverage = coverage_tracker.run(output)
                    coverage_gain = status_cov == ExecutionStatus.SUCCESS and new_coverage

                feedback = (
                    guidance.build_feedback(output, api, report.status, report.message, coverage_gain)
                    if guidance is not None
                    else _fallback_feedback(report, target_hit, coverage_gain)
                )
                all_feedbacks.append(feedback)

                if num_replaced < 1 or not target_hit:
                    subfolder = "notarget"
                    dump_code = '/*\n{}\n{}\n*/\n{}'.format(str(report.status), report.message, output)
                    with open(
                        os.path.join(args.folder, subfolder, api + "_" + str(num_generated) + ".c"),
                        "w",
                        encoding="utf-8",
                    ) as handle:
                        handle.write(dump_code)
                    num_notarget += 1
                    continue

                dump_code = output
                subfolder = ""
                if report.run_ok:
                    subfolder = "valid"
                elif report.status == ExecutionStatus.TIMEOUT:
                    num_timeout += 1
                    subfolder = "hangs"
                    dump_code = '/*\n{}\n*/\n{}'.format(report.message, output)
                elif report.status == ExecutionStatus.CRASH:
                    num_crash += 1
                    subfolder = "crash"
                    crashes.append(output)
                    dump_code = '/*\n{}\n*/\n{}'.format(report.message, output)
                else:
                    num_exception += 1
                    subfolder = "exception"
                    dump_code = '/*\n{}\n*/\n{}'.format(report.message, output)

                filename = os.path.join(args.folder, subfolder, api + "_" + str(num_generated) + ".c")
                with open(filename, "w", encoding="utf-8") as handle:
                    handle.write(dump_code)

                if report.run_ok:
                    round_valid += 1
                    generations.append(output)
                    filenames.append(filename)
                    generation_feedbacks.append(feedback)
                    add_flags.append(coverage_gain)

            ga.update(
                seed,
                generations,
                replace_type,
                round_id,
                filenames,
                generation_feedbacks=generation_feedbacks,
                feedbacks=all_feedbacks,
                add_flags=add_flags,
            )

        num_valid += round_valid
        generation_time.append(round_generation)
        validation_time.append(round_validation)
        total_run_time.append(time.time() - start_time_total)
        round_id += 1
        if round_valid == 0:
            stagnation_rounds += 1
        else:
            stagnation_rounds = 0
        logger.logo(
            "--- New Valid : {} using {:.2f}s generation, {:.2f}s validation ---".format(
                round_valid,
                round_generation,
                round_validation,
            )
        )
        if stagnation_rounds >= args.max_stagnation_rounds:
            logger.logo("--- Stop after {} stagnant rounds ---".format(stagnation_rounds))
            break

    best_code, highest_order = ga.get_highest_order_output()
    logger.logo("Highest Order: {}".format(highest_order))
    if best_code:
        logger.logo("-----\n{}\n-----".format(best_code))
    logger.logo(
        "{} valid outputs using {:.2f}s generation, {:.2f}s validation".format(
            num_valid,
            sum(generation_time),
            sum(validation_time),
        )
    )
    logger.logo(
        "{} generated: {} exceptions {} duplicated {} crashes {} timeouts {} notarget".format(
            num_generated,
            num_exception,
            num_duplicated,
            num_crash,
            num_timeout,
            num_notarget,
        )
    )

    return {
        "outputs": ga.info_code,
        "p": ga.get_p(),
        "crashes": crashes,
        "g_time": generation_time,
        "v_time": validation_time,
        "tot_time": total_run_time,
    }


def generate(args, model, target_adapter):
    os.makedirs(args.folder, exist_ok=True)
    for subfolder in ["seed", "valid", "hangs", "crash", "exception", "notarget"]:
        os.makedirs(os.path.join(args.folder, subfolder), exist_ok=True)
    with open(os.path.join(args.folder, "args.txt"), "w", encoding="utf-8") as handle:
        handle.write(str(args))

    logger = Logger(args.folder)
    guidance = RiskGuidance(target_adapter.metadata) if target_adapter.metadata is not None else None
    full_api_list = load_apis(target_adapter.get_api_list_path())
    apis = get_initial_programs(
        args.seedfolder,
        model.infill_ph,
        target_adapter,
        "argument",
        target_api=args.api,
    )
    if not apis:
        seed_root = Path(args.seedfolder)
        if args.api == "all":
            expected = "{}/*/*.c".format(seed_root)
        else:
            expected = "{}/{}/{}.c".format(seed_root, args.api, "*")
        raise RuntimeError(
            "no mutable seeds found. Expected validated seed files matching {}. "
            "Run seed generation first and check runtime_data/seed_generation/summary.json "
            "for total_valid_seeds > 0.".format(expected)
        )

    selected_apis = list(apis.keys())
    if args.apilist is not None:
        requested = load_apis(args.apilist)
        selected_apis = [api for api in requested if api in apis]
    if not selected_apis:
        raise RuntimeError(
            "no selected APIs have mutable seeds. requested api={!r}, discovered seed APIs={}".format(
                args.api,
                ", ".join(sorted(apis)) or "<none>",
            )
        )

    gen_ret = {}
    for api in selected_apis:
        seeds = apis.get(api, [])
        if len(seeds) == 0:
            continue
        logger.logo("--- Generating for {} ---".format(api))
        logger.logo("------ | seeds | = {} -----".format(len(seeds)))
        seeds_for_generation = []
        for idx, seed in enumerate(seeds):
            report = validate_testcase(
                seed["original"],
                target_adapter=target_adapter,
                target_root=args.target_root,
                compiler=args.compiler,
                build_root=BUILD_ROOT,
                compile_timeout=args.compile_timeout,
                test_timeout=args.test_timeout,
                enable_sanitizer=args.enable_sanitizer,
            )
            with open(
                os.path.join(args.folder, "seed", api + "_seed{}.c".format(idx + 1)),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(seed["original"])
            if report.run_ok or not args.only_valid:
                seeds_for_generation.append(seed["original"])
        logger.logo("--- seeds_for_generation : {} ---".format(len(seeds_for_generation)))
        if len(seeds_for_generation) == 0:
            continue
        gen_ret[api] = {
            "seeds": seeds_for_generation,
            "initials": seeds_for_generation,
        }
        gen_ret[api].update(
            generate_loop(
                args,
                model,
                target_adapter,
                guidance,
                full_api_list,
                seeds_for_generation,
                api,
                logger,
                args.max_valid,
            )
        )
        with open(os.path.join(args.folder, "outputs.json"), "w", encoding="utf-8") as handle:
            json.dump(gen_ret, handle, indent=2)

    if hasattr(model, "get_usage_summary"):
        for api_record in gen_ret.values():
            if isinstance(api_record, dict):
                api_record["token_usage"] = model.get_usage_summary()
        with open(os.path.join(args.folder, "outputs.json"), "w", encoding="utf-8") as handle:
            json.dump(gen_ret, handle, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm_provider", type=str, default="mock", choices=["mock", "local_hf", "deepseek", "openai_compatible"])
    parser.add_argument("--model_name", type=str, default=None)
    parser.add_argument("--llm_api_base", type=str, default=None)
    parser.add_argument("--llm_api_key", type=str, default=None)
    parser.add_argument("--llm_request_timeout", type=int, default=60)
    parser.add_argument("--llm_max_tokens", type=int, default=256)
    parser.add_argument("--llm_temperature", type=float, default=1.0)
    parser.add_argument("--target", type=str, default="generic", choices=["generic", "auto"])
    parser.add_argument("--target_root", type=str, default=None)
    parser.add_argument("--api", type=str, default="all")
    parser.add_argument("--apilist", type=str, default=None)
    parser.add_argument("--folder", type=str, default=str(Path("mutation") / "Results" / "test"))
    parser.add_argument("--seedfolder", type=str, default=None)
    parser.add_argument("--random_seed", type=int, default=420)
    parser.add_argument("--max_valid", type=int, default=50)
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--num_selection", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--seed_pool_size", type=int, default=20)
    parser.add_argument("--only_valid", action="store_true", default=False)
    parser.add_argument("--relaxargmut", action="store_true", default=False)
    parser.add_argument("--seed_selection_algo", type=str, default="fitness", choices=["fitness", "random", "coverage"])
    parser.add_argument("--mutator_selection_algo", type=str, default="ts", choices=["heuristic", "epsgreedy", "ucb", "random", "ts"])
    parser.add_argument("--use_single_mutator", action="store_true", default=False)
    parser.add_argument("--replace_type", type=str, default=None)
    parser.add_argument("--mutator_set", type=str, default="all", choices=["all", "noprefix", "nosuffix", "noargument", "nomethod"])
    parser.add_argument("--compiler", type=str, default="clang")
    parser.add_argument("--coverage-tool", dest="coverage_tool", type=str, default=default_coverage_tool())
    parser.add_argument("--compile-timeout", dest="compile_timeout", type=int, default=20)
    parser.add_argument("--test-timeout", dest="test_timeout", type=int, default=10)
    parser.add_argument("--enable-sanitizer", dest="enable_sanitizer", action="store_true", default=False)
    parser.add_argument("--max_stagnation_rounds", type=int, default=25)
    parser.add_argument("--max_rounds", type=int, default=0, help="Stop after this many mutation rounds (0 disables).")
    parser.add_argument("--mutation-signature-only-prompt", action="store_true", default=False)
    parser.add_argument("--mutation-no-execution-context", action="store_true", default=False)
    parser.add_argument("--mutation-no-risk-context", action="store_true", default=False)
    args = parser.parse_args()
    args.compiler = normalize_clang_compiler(args.compiler, source="mutation --compiler")
    configure_clang_environment(compiler=args.compiler, enable_sanitizer=args.enable_sanitizer)

    package_root = Path(__file__).resolve().parent
    target_adapter = create_target_adapter(
        args.target,
        package_root,
        default_target_root=args.target_root,
        include_execution_context=not args.mutation_no_execution_context,
        include_risk_context=not args.mutation_no_risk_context,
        signature_only_prompt=args.mutation_signature_only_prompt,
    )
    args.target_root = str(target_adapter.resolve_target_root(args.target_root))
    target_adapter.ensure_prepared(args.target_root)
    if args.seedfolder is None:
        args.seedfolder = str(target_adapter.get_seed_root())
    if args.apilist is None and args.api == "all":
        args.apilist = str(target_adapter.get_api_list_path())

    set_seed(args.random_seed)
    model = create_llm_client(args, target_adapter)
    generate(args, model, target_adapter)


if __name__ == "__main__":
    main()
