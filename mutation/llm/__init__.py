from __future__ import annotations

import os


def create_llm_client(args, target_adapter):
    provider = getattr(args, "llm_provider", "mock")
    if provider == "mock":
        from ctitanfuzz.llm.mock import MockInfillLLM

        return MockInfillLLM(target_adapter)
    if provider == "local_hf":
        from ctitanfuzz.llm.local_hf import LocalHuggingFaceLLM

        model_name = args.model_name or "facebook/incoder-1B"
        return LocalHuggingFaceLLM(model_name, batch_size=args.batch_size)
    if provider == "deepseek":
        from ctitanfuzz.llm.openai_compatible import DeepSeekInfillLLM

        model_name = args.model_name or "deepseek-chat"
        return DeepSeekInfillLLM(
            model_name=model_name,
            api_base=args.llm_api_base or "https://api.deepseek.com",
            api_key=args.llm_api_key or os.environ.get("DEEPSEEK_API_KEY"),
            timeout=args.llm_request_timeout,
            max_tokens=args.llm_max_tokens,
            temperature=args.llm_temperature,
        )
    if provider == "openai_compatible":
        from ctitanfuzz.llm.openai_compatible import OpenAICompatibleInfillLLM

        api_key = args.llm_api_key or os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise ValueError("LLM API key is missing. Set --llm_api_key or LLM_API_KEY.")
        if not args.llm_api_base:
            raise ValueError("--llm_api_base is required for openai_compatible provider.")
        model_name = args.model_name or "gpt-4o-mini"
        return OpenAICompatibleInfillLLM(
            model_name=model_name,
            api_base=args.llm_api_base,
            api_key=api_key,
            timeout=args.llm_request_timeout,
            max_tokens=args.llm_max_tokens,
            temperature=args.llm_temperature,
        )
    raise ValueError("Unsupported llm_provider: {}".format(provider))
