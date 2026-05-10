from __future__ import annotations

from pathlib import Path

from mutation.metadata import ApiSpec, build_library_metadata, render_seed_context
from mutation.seed_builder import build_auto_seed_corpus
from mutation.targets.base import TargetAdapter


class GenericCTargetAdapter(TargetAdapter):
    name = "generic"

    def __init__(
        self,
        package_root: str | Path,
        *,
        default_target_root: str | Path | None = None,
    ) -> None:
        super().__init__(package_root)
        if default_target_root is not None:
            self.default_target_root = Path(default_target_root).resolve()
        self._seed_root: Path | None = None

    def _prepare_impl(self, target_root: Path) -> None:
        self.metadata = build_library_metadata(
            self.package_root,
            target_root,
            library_name=None if self.name == "generic" else self.name,
        )
        self._seed_root = build_auto_seed_corpus(self.metadata)

    def get_seed_root(self) -> Path:
        self.ensure_prepared()
        assert self._seed_root is not None
        return self._seed_root

    def get_api_list_path(self, demo: bool = False) -> Path:
        self.ensure_prepared()
        assert self.metadata is not None
        return self.metadata.api_list_path

    def get_api_defs_path(self) -> Path:
        self.ensure_prepared()
        assert self.metadata is not None
        return self.metadata.api_defs_path

    def _fallback_header(self) -> str:
        assert self.metadata is not None
        if self.metadata.public_headers:
            return self.metadata.public_headers[0].relative_to(self.metadata.target_root).as_posix()
        return ""

    def get_import_hint(self, api: str) -> str:
        self.ensure_prepared()
        assert self.metadata is not None
        spec = self.metadata.get_api_spec(api)
        header = spec.header if spec is not None else self._fallback_header()
        return '#include "{}"'.format(header) if header else ""

    def get_target_sources(self, target_root: Path) -> list[Path]:
        self.ensure_prepared(target_root)
        assert self.metadata is not None
        return self.metadata.source_files

    def get_include_dirs(self, target_root: Path) -> list[Path]:
        self.ensure_prepared(target_root)
        assert self.metadata is not None
        return self.metadata.include_dirs

    def get_focus_files(self, target_root: Path) -> list[Path]:
        self.ensure_prepared(target_root)
        assert self.metadata is not None
        return self.metadata.focus_files

    def _render_neighbor_examples(self, api: str) -> list[str]:
        assert self.metadata is not None
        seed_context = self.metadata.build_seed_context(api)
        examples: list[str] = []
        argument_bank = self.get_mock_argument_bank()
        neighbors = list(
            dict.fromkeys(seed_context.risk_high_risk_neighbors + seed_context.execution_neighbor_apis)
        )[:3]
        for neighbor in neighbors:
            spec = self.metadata.get_api_spec(neighbor)
            if spec is None:
                continue
            arg_variants = argument_bank.get(neighbor, [])
            args = arg_variants[0] if arg_variants else ""
            setup_hints: list[str] = []
            if "&parse_end" in args or "parse_end" in args:
                setup_hints.append("const char *parse_end = NULL;")
            if "input_length" in args:
                setup_hints.append("size_t input_length = strlen(input_text);")
            if spec.ret != "void":
                call_expr = "{} path_result = {}({});".format(spec.ret, neighbor, args).strip()
            else:
                call_expr = "{}({});".format(neighbor, args)
            pieces = setup_hints + [call_expr]
            examples.append("- {}".format(" ".join(piece for piece in pieces if piece)))
        return examples

    def render_generation_guidance(self, api: str) -> str:
        self.ensure_prepared()
        assert self.metadata is not None
        if api not in self.metadata.api_specs:
            return "Task 6: keep resource ownership valid and preserve the target API call."
        seed_context = self.metadata.build_seed_context(api)
        guidance = (
            "Task 6: preserve init and cleanup correctness while keeping the target API call.\n"
            "Task 7: prefer 1-2 boundary cases from the risk hints.\n"
            "Task 8: if high-risk neighbor APIs exist, you may add one short neighbor call when it fits naturally; prefer an executed call from main or from a helper that main invokes, but skip it rather than forcing an awkward chain.\n"
            "Task 9: do not duplicate includes, do not leave unused helpers, do not copy RALFUZZ prompt comments into the final code, and do not call delete/free twice on the same pointer.\n"
            "Context:\n"
            + render_seed_context(seed_context)
        )
        neighbor_examples = self._render_neighbor_examples(api)
        if neighbor_examples:
            guidance += "\nNeighbor call examples:\n" + "\n".join(neighbor_examples)
        return guidance

    def _guess_builder_var(self, spec: ApiSpec) -> str:
        api_lower = spec.api.lower()
        if "parse" in api_lower:
            return "root"
        if "create" in api_lower or "new" in api_lower:
            return "item"
        return "{}_obj".format(spec.api.lower())

    def _pointer_arg_candidates(self, api_name: str, arg_name: str, arg_type: str) -> list[str]:
        lowered_api = api_name.lower()
        lowered_name = arg_name.lower()
        base_type = arg_type.replace("const", "").replace("*", " ").strip().lower()
        if "parse_end" in lowered_name or "end" in lowered_name:
            return ["&parse_end", "NULL"]
        if any(token in lowered_api for token in ("delete", "free", "destroy", "release", "cleanup", "close")):
            return ["result", "root", "item", "item_obj"]
        if "json" in base_type or "item" in lowered_name or "root" in lowered_name:
            return ["root", "item", "item_obj", "result"]
        if "ctx" in lowered_name or "context" in lowered_name:
            return ["ctx", "context", "result"]
        return ["result", "obj", "ctx"]

    def get_mock_function_bank(self) -> list[str]:
        self.ensure_prepared()
        assert self.metadata is not None
        def rank(api_name: str) -> tuple[float, float, str]:
            risk = self.metadata.get_risk_profile(api_name)
            cg = self.metadata.get_call_graph_entry(api_name)
            return (
                float(risk.risk_level) if risk is not None else 0.0,
                float(cg.cg_priority) if cg is not None else 0.0,
                api_name,
            )

        ranked = sorted(
            self.metadata.api_specs,
            key=lambda api_name: (-rank(api_name)[0], -rank(api_name)[1], rank(api_name)[2]),
        )
        return ranked[:128]

    def get_mock_statement_bank(self) -> list[str]:
        return [
            "/* mock no-op */",
            'const char *input_text = "";',
            "if (input_text == NULL) { return 0; }",
            "const char *parse_end = NULL;",
            "if (result == NULL) { return 0; }",
            "size_t input_length = strlen(input_text);",
            "if (mutable_buffer[0] == '\\0') { return 0; }",
        ]

    def get_mock_argument_bank(self) -> dict[str, list[str]]:
        self.ensure_prepared()
        assert self.metadata is not None
        bank: dict[str, list[str]] = {}
        for api_name, spec in self.metadata.api_specs.items():
            if not spec.arg_types:
                bank[api_name] = [""]
                continue

            candidate_lists: list[list[str]] = []
            for arg_name, arg_type in zip(spec.arg_names, spec.arg_types):
                lowered = "{} {}".format(arg_name, arg_type).lower()
                if "char" in arg_type and "**" in arg_type:
                    candidate_lists.append(["&parse_end", "NULL"])
                elif "char" in arg_type and "*" in arg_type:
                    candidate_lists.append(["input_text", "mutable_buffer"] if "const" in arg_type else ["mutable_buffer", "input_text"])
                elif any(token in lowered for token in ("len", "length", "size", "count")):
                    candidate_lists.append(["input_length", "strlen(input_text)", "0"])
                elif any(token in lowered for token in ("idx", "index", "offset")):
                    candidate_lists.append(["0", "1"])
                elif "*" in arg_type:
                    candidate_lists.append(self._pointer_arg_candidates(api_name, arg_name, arg_type))
                elif any(token in lowered for token in ("bool", "flag", "require")):
                    candidate_lists.append(["0", "1"])
                else:
                    candidate_lists.append(["1", "0"])

            rendered: list[str] = []
            max_candidates = min(max(len(candidates) for candidates in candidate_lists), 3)
            for idx in range(max_candidates):
                pieces = []
                for candidates in candidate_lists:
                    pieces.append(candidates[min(idx, len(candidates) - 1)])
                rendered.append(", ".join(pieces))
            bank[api_name] = list(dict.fromkeys(rendered))
        return bank
