from __future__ import annotations

from ctitanfuzz.c_mutators import (
    DepthFinder,
    SnippetInfill,
    SnippetInfillArbitraryAPI,
    UniqueFinder,
    find_call_spans,
)


class SearchAllCall:
    def __init__(self, api_names=None):
        self.api_names = api_names or []

    def search_from_code(self, snippet) -> list:
        return [(span, span.api_name) for span in find_call_spans(snippet, self.api_names)]


class SearchAllLibCall:
    def __init__(self, lib_prefix: str, api_names=None):
        self.lib_prefix = lib_prefix
        self.api_names = api_names or []

    def search_from_code(self, snippet, api_names=None) -> list:
        names = api_names or self.api_names
        return [(span, span.api_name) for span in find_call_spans(snippet, names)]


SnippetInfillArbitratyAPI = SnippetInfillArbitraryAPI

__all__ = [
    "DepthFinder",
    "UniqueFinder",
    "SnippetInfill",
    "SnippetInfillArbitraryAPI",
    "SnippetInfillArbitratyAPI",
    "SearchAllCall",
    "SearchAllLibCall",
]
