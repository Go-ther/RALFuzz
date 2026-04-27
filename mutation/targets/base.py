from __future__ import annotations

from pathlib import Path

from ctitanfuzz.metadata import LibraryMetadata


DEFAULT_STEP_PROMPT_TEMPLATE = """Task 1: add the required C headers for {} and standard helpers.
Task 2: generate concrete inputs, buffers, pointers, and local state.
Task 3: call the target API {}.
Task 4: preserve simple sanity checks and round-trip checks when possible.
Task 5: release owned resources before returning from main.
"""


class TargetAdapter:
    name = "base"
    file_extension = ".c"
    default_seed_subdir = ""
    default_api_list_file = ""
    default_demo_api_list_file = ""
    default_api_defs_file = ""

    def __init__(self, package_root: str | Path) -> None:
        self.package_root = Path(package_root).resolve()
        self.data_dir = self.package_root / "data"
        self.prompts_dir = self.data_dir / "prompts"
        self.seeds_dir = self.package_root / "seeds"
        self.default_target_root: Path | None = None
        self.current_target_root: Path | None = None
        self.metadata: LibraryMetadata | None = None

    def resolve_target_root(self, target_root: str | Path | None) -> Path:
        if target_root is None:
            if self.default_target_root is None:
                raise ValueError("target root is required. Pass --target_root or --api-dir explicitly.")
            return self.default_target_root
        return Path(target_root).resolve()

    def ensure_prepared(self, target_root: str | Path | None = None) -> None:
        resolved_target = target_root
        if resolved_target is None and self.current_target_root is not None:
            resolved_target = self.current_target_root
        resolved = self.resolve_target_root(resolved_target)
        if self.current_target_root == resolved and self.metadata is not None:
            return
        self._prepare_impl(resolved)
        self.current_target_root = resolved

    def _prepare_impl(self, target_root: Path) -> None:
        self.current_target_root = target_root

    def get_seed_root(self) -> Path:
        return self.seeds_dir / self.default_seed_subdir

    def get_api_list_path(self, demo: bool = False) -> Path:
        filename = self.default_demo_api_list_file if demo else self.default_api_list_file
        return self.data_dir / filename

    def get_api_defs_path(self) -> Path:
        return self.data_dir / self.default_api_defs_file

    def get_step_prompt_template(self) -> str:
        prompt_path = self.prompts_dir / "step_to_step_c.txt"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return DEFAULT_STEP_PROMPT_TEMPLATE

    def build_step_prompt(self, api: str) -> str:
        prompt = self.get_step_prompt_template().format(self.get_import_hint(api), api)
        guidance = self.render_generation_guidance(api)
        if guidance:
            prompt += "\n" + guidance
        return prompt

    def attach_generation_context(self, api: str, infill_code: str) -> str:
        prompt = self.build_step_prompt(api)
        return (
            "/* CTITANFUZZ_STEP_PROMPT_BEGIN\n"
            + prompt
            + "\nCTITANFUZZ_STEP_PROMPT_END */\n"
            + infill_code
        )

    def strip_generation_context(self, code: str) -> str:
        marker_begin = "/* CTITANFUZZ_STEP_PROMPT_BEGIN"
        marker_end = "CTITANFUZZ_STEP_PROMPT_END */"
        if marker_begin not in code:
            return code
        begin = code.find(marker_begin)
        end = code.find(marker_end, begin)
        if end == -1:
            return code
        end += len(marker_end)
        if end < len(code) and code[end] == "\n":
            end += 1
        return code[:begin] + code[end:]

    def render_generation_guidance(self, api: str) -> str:
        return ""

    def get_import_hint(self, api: str) -> str:
        raise NotImplementedError

    def get_target_sources(self, target_root: Path) -> list[Path]:
        raise NotImplementedError

    def get_include_dirs(self, target_root: Path) -> list[Path]:
        return [target_root]

    def get_common_cflags(
        self,
        enable_coverage: bool = False,
        enable_sanitizer: bool = False,
    ) -> list[str]:
        flags = ["-std=c11", "-O0", "-Wall", "-Wextra"]
        if enable_coverage:
            flags.extend(["-fprofile-arcs", "-ftest-coverage"])
        if enable_sanitizer:
            flags.extend(["-fsanitize=address", "-fsanitize=undefined"])
        return flags

    def get_link_flags(
        self,
        enable_coverage: bool = False,
        enable_sanitizer: bool = False,
    ) -> list[str]:
        flags: list[str] = []
        if enable_coverage:
            flags.extend(["-fprofile-arcs", "-ftest-coverage"])
        if enable_sanitizer:
            flags.extend(["-fsanitize=address", "-fsanitize=undefined"])
        return flags

    def get_focus_files(self, target_root: Path) -> list[Path]:
        raise NotImplementedError

    def classify_oracle_failure(self, stdout_stderr: str) -> tuple[str | None, str]:
        if "CTITANFUZZ_ORACLE:" in stdout_stderr:
            return "OracleFailCatch", stdout_stderr
        return None, stdout_stderr

    def get_mock_function_bank(self) -> list[str]:
        raise NotImplementedError

    def get_mock_statement_bank(self) -> list[str]:
        raise NotImplementedError

    def get_mock_argument_bank(self) -> dict[str, list[str]]:
        raise NotImplementedError
