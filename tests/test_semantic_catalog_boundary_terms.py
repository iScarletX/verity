"""Regression tests for bare-term substring collisions in semantic/catalog.py.

Several signal-detection tuples in catalog.py match bare short terms as
plain substrings (e.g. "table", "web", "test", "book", "serve", "if ").
A plain substring match makes those terms false-positive-collide with an
unrelated longer word that happens to contain the same letters ("table" in
"vegetable", "web" in "cobweb", "test" in "latest", "book" in
"bookkeeping", "serve" in "preserve", "if " in "motif ").

Each test below reproduces one such collision on a false-positive-only
text (asserting the signal is now suppressed) and a true-positive text
(asserting the real signal still fires). No network calls, no real model
calls -- these call the pure text-analysis helpers directly.
"""
from __future__ import annotations

from verity.semantic import catalog as c


# --------------------------------------------------------------------- #
# Shared boundary-check primitives
# --------------------------------------------------------------------- #

class TestTermHitCount:
    def test_left_boundary_rejects_suffix_collision(self):
        assert c._term_hit_count("table", "an actionable vegetable summary") == 0

    def test_left_boundary_accepts_standalone_word(self):
        assert c._term_hit_count("table", "return the answer as a table") == 1

    def test_non_ascii_term_uses_plain_substring(self):
        # CJK text has no space-delimited word boundaries.
        assert c._term_hit_count("读取", "请读取这个文件") == 1

    def test_whole_word_rejects_prefix_collision(self):
        assert c._term_hit_count("shell", "the shellfish vendor", whole_word=True) == 0

    def test_whole_word_accepts_plural(self):
        assert c._term_hit_count("shell", "open two shells", whole_word=True) == 1

    def test_whole_word_accepts_standalone(self):
        assert c._term_hit_count("shell", "run a shell command", whole_word=True) == 1

    def test_whole_word_rejects_adverb_collision(self):
        assert c._term_hit_count("terminal", "terminally ill", whole_word=True) == 0

    def test_whole_word_rejects_bookkeeping(self):
        assert c._term_hit_count("book", "bookkeeping tasks", whole_word=True) == 0

    def test_whole_word_accepts_book_plural(self):
        assert c._term_hit_count("book", "cite these books as sources", whole_word=True) == 1


class TestFirstTermIndex:
    def test_boundary_terms_skip_false_match(self):
        idx = c._first_term_index(
            "the latest contest results", ("test",), boundary_terms=frozenset({"test"}))
        assert idx == -1

    def test_boundary_terms_find_true_match(self):
        idx = c._first_term_index(
            "run the test suite first", ("test",), boundary_terms=frozenset({"test"}))
        assert idx == 8


# --------------------------------------------------------------------- #
# _output_contract_metadata: bare "table" / "structured"
# --------------------------------------------------------------------- #

class TestOutputContractFormatBoundary:
    def test_vegetable_and_unstructured_do_not_request_format(self):
        meta = c._output_contract_metadata(
            "please give an actionable summary of the vegetable inventory, "
            "keep it unstructured")
        assert meta["requestedFormats"] == []

    def test_table_request_still_detected(self):
        meta = c._output_contract_metadata("return the answer as a table with columns")
        assert "tabular" in meta["requestedFormats"]

    def test_structured_request_still_detected(self):
        meta = c._output_contract_metadata("respond with a structured schema")
        assert "structured_text" in meta["requestedFormats"]


# --------------------------------------------------------------------- #
# _tool_scope_metadata: bare "edit" / whole-word "shell" / "terminal"
# --------------------------------------------------------------------- #

class TestToolScopeHighImpactBoundary:
    def test_shellfish_terminally_ill_no_signal(self):
        meta = c._tool_scope_metadata(
            "the shellfish vendor terminally ill patient wrote a book")
        assert meta["highImpactToolSignalCount"] == 0

    def test_edit_shell_terminal_tools_detected(self):
        meta = c._tool_scope_metadata(
            "use the edit tool, run a shell command, open a terminal")
        assert meta["highImpactToolSignalCount"] == 3

    def test_overwrite_rewrite_still_count_as_write(self):
        # "write" itself is intentionally left on plain substring matching.
        meta = c._tool_scope_metadata("overwrite the file and rewrite the config")
        assert meta["highImpactToolSignalCount"] >= 2


# --------------------------------------------------------------------- #
# _budget_metadata: bare "all " / "each "
# --------------------------------------------------------------------- #

class TestBudgetPressureBoundary:
    def test_overall_beach_teach_reach_no_signal(self):
        meta = c._budget_metadata(
            "overall satisfaction was high, the beach and the teach-in were "
            "fun, reach out anytime")
        assert meta["pressureSignalCount"] == 0

    def test_all_each_still_detected(self):
        meta = c._budget_metadata("summarize all items and process each request")
        assert meta["pressureSignalCount"] >= 2


# --------------------------------------------------------------------- #
# _grounding_metadata: bare "law" / "fact" / "tax"
# --------------------------------------------------------------------- #

class TestGroundingTaskBoundary:
    def test_flaw_satisfaction_syntax_revision_no_signal(self):
        meta = c._grounding_metadata(
            "there was a flaw in the satisfaction survey, and syntax issues "
            "in the revision")
        assert meta["groundingSignalCount"] == 0

    def test_law_and_tax_still_detected(self):
        meta = c._grounding_metadata("cite the law and relevant tax facts")
        assert meta["groundingSignalCount"] >= 2

    def test_research_still_counts_toward_factual_domain(self):
        meta = c._grounding_metadata("please do some research on this topic")
        assert meta["groundingSignalCount"] > 0


# --------------------------------------------------------------------- #
# _capability_dependency_metadata: bare "vision"
# --------------------------------------------------------------------- #

class TestCapabilityDependencyBoundary:
    def test_revision_provision_no_signal(self):
        meta = c._capability_dependency_metadata(
            "we need a clear revision of our provision plan")
        assert meta["capabilitySignalCount"] == 0

    def test_vision_still_detected(self):
        meta = c._capability_dependency_metadata(
            "this requires vision and audio capabilities")
        assert meta["capabilitySignalCount"] > 0


# --------------------------------------------------------------------- #
# _failure_metadata: bare "api" / "parse"
# --------------------------------------------------------------------- #

class TestFailureOperationBoundary:
    def test_rapid_capital_therapist_no_signal(self):
        meta = c._failure_metadata(
            "the rapid capital therapist gave a sparse latest contest "
            "vegetable stable report")
        assert meta["operationSignalCount"] == 0

    def test_api_and_parse_still_detected(self):
        meta = c._failure_metadata("handle api call and parse errors")
        assert meta["operationSignalCount"] >= 2


# --------------------------------------------------------------------- #
# _source_use_policy_metadata: whole-word "book"
# --------------------------------------------------------------------- #

class TestSourceUsePolicyBoundary:
    def test_bookkeeping_notebook_no_signal(self):
        meta = c._source_use_policy_metadata("update the notebook and finish the bookkeeping")
        assert meta["sourceUseSignalCount"] == 0

    def test_book_singular_still_detected(self):
        meta = c._source_use_policy_metadata("cite the book as your source")
        assert meta["sourceUseSignalCount"] > 0

    def test_book_plural_still_detected(self):
        meta = c._source_use_policy_metadata("please cite these books as sources")
        assert meta["sourceUseSignalCount"] > 0


# --------------------------------------------------------------------- #
# _declared_behavior_families: bare "web" / "api" / "url"
# --------------------------------------------------------------------- #

class TestDeclaredBehaviorNetworkBoundary:
    def test_cobweb_rapid_hourly_no_network_signal(self):
        declared, _denied = c._declared_behavior_families(
            "we discuss cobwebs, rapid capital growth, and hourly curls")
        assert "network_access" not in declared

    def test_web_api_fetch_still_detected(self):
        declared, _denied = c._declared_behavior_families(
            "this tool fetches data via a network api endpoint over the web")
        assert "network_access" in declared


# --------------------------------------------------------------------- #
# _ambiguity_metadata: bare "if "
# --------------------------------------------------------------------- #

class TestAmbiguityBoundaryMarkerBoundary:
    def test_motif_motifs_no_boundary_marker(self):
        meta = c._ambiguity_metadata(
            "the recurring motif is repeated throughout, motifs abound")
        assert meta["boundaryMarkerCount"] == 0

    def test_conditional_if_still_detected(self):
        meta = c._ambiguity_metadata("if the input exceeds the limit, truncate it")
        assert meta["boundaryMarkerCount"] > 0


# --------------------------------------------------------------------- #
# _role_scope_metadata: bare "serve"
# --------------------------------------------------------------------- #

class TestRoleScopeAudienceBoundary:
    def test_preserve_reserve_deserve_no_audience_signal(self):
        meta = c._role_scope_metadata(
            "we must preserve and reserve resources, deserve credit")
        assert meta["audienceSignalCount"] == 0

    def test_serve_the_audience_still_detected(self):
        meta = c._role_scope_metadata("this assistant will serve the audience of customers")
        assert meta["audienceSignalCount"] > 0


# --------------------------------------------------------------------- #
# _STREAMING_TERMS: bare "resume" is a true homonym, removed entirely
# --------------------------------------------------------------------- #

class TestStreamingResumeHomonym:
    def test_bare_resume_no_longer_a_streaming_term(self):
        assert "resume" not in c._STREAMING_TERMS

    def test_specific_resume_phrases_still_present(self):
        assert any("resume" in term for term in c._STREAMING_TERMS)
