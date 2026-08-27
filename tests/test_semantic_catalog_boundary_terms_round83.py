"""Regression tests for the second wave of bare-term substring collisions
in semantic/catalog.py (Round 83).

Same bug class as test_semantic_catalog_boundary_terms.py (Round 82): a
signal-detection tuple matches a bare short term as a plain substring, so
it false-positive-collides with an unrelated longer word that happens to
contain the same letters -- either as a SUFFIX ("date" in "update", "cite"
in "excite", "unit" in "community", "range" in "strange", "list" in
"enlist", "print" in "footprint", "store" in "drugstore", "session" in
"possession") or as a PREFIX ("object" in "objective", "must" in
"mustache", "block" in "blockchain", "import" in "important").

Each test reproduces one collision on a false-positive-only text (signal
suppressed) and a true-positive text (real signal still fires). No network
calls, no real model calls -- pure text-analysis helpers only.
"""
from __future__ import annotations

from verity.semantic import catalog as c


# --------------------------------------------------------------------- #
# _output_contract_metadata / _TYPE_TERMS, _UNIT_TERMS: object/list/unit
# --------------------------------------------------------------------- #

class TestOutputContractTypeUnitBoundary:
    def test_objective_enlist_no_type_signal(self):
        meta = c._output_contract_metadata(
            "the objective is clear, no objections, we will enlist volunteers")
        assert meta["typeMarkerCount"] == 0

    def test_object_and_list_still_detected(self):
        meta = c._output_contract_metadata(
            "return a json object and a list of items")
        assert meta["typeMarkerCount"] == 2

    def test_community_immunity_no_unit_signal(self):
        meta = c._output_contract_metadata(
            "for the community guidelines, immunity boost")
        assert meta["unitMarkerCount"] == 0

    def test_unit_still_detected(self):
        meta = c._output_contract_metadata("specify the unit of measure")
        assert meta["unitMarkerCount"] == 1


# --------------------------------------------------------------------- #
# _field_constraint_metadata / _FIELD_TYPE_TERMS, _FIELD_UNIT_PRECISION_TERMS,
# _FIELD_RANGE_TERMS, _FIELD_CONTRACT_TERMS: object/list/unit/range/date
# --------------------------------------------------------------------- #

class TestFieldConstraintBoundary:
    def test_collision_words_no_field_signals(self):
        meta = c._field_constraint_metadata(
            "the objective is clear, no objections, we will enlist "
            "volunteers, for the community guidelines, immunity boost, "
            "that is a strange arrangement, please update the record, "
            "validate the mandate")
        assert meta["fieldTypeSignalCount"] == 0
        assert meta["unitPrecisionSignalCount"] == 0
        assert meta["rangeSignalCount"] == 0
        assert meta["fieldSignalCount"] == 0

    def test_real_field_terms_still_detected(self):
        meta = c._field_constraint_metadata(
            "return a json object and a list of items, specify the unit "
            "of measure, set the numeric range, the due date is set")
        assert meta["fieldTypeSignalCount"] >= 2
        assert meta["unitPrecisionSignalCount"] >= 1
        assert meta["rangeSignalCount"] >= 1
        assert meta["fieldSignalCount"] >= 1


# --------------------------------------------------------------------- #
# _grounding_metadata / _GROUNDING_CONTROL_TERMS: cite
# --------------------------------------------------------------------- #

class TestGroundingControlCiteBoundary:
    def test_excite_no_mitigation_signal(self):
        meta = c._grounding_metadata("this response should excite the reader")
        assert meta["mitigationSignalCount"] == 0

    def test_cite_still_detected(self):
        meta = c._grounding_metadata("please cite the source")
        assert meta["mitigationSignalCount"] >= 1

    def test_excite_does_not_mark_grounding_task_as_covered(self):
        # A grounding task ("law"/"tax") with only a false "cite" match
        # (from "excite") must still count as uncovered -- the control-term
        # boundary check applies inside _scoped_gap_count too, not just the
        # whole-document mitigationSignalCount tally.
        text = ("the law requires tax facts to be reported; this may "
                "excite the reader.")
        total, uncovered = c._scoped_gap_count(
            text,
            signal_groups=(c._GROUNDING_TASK_TERMS,),
            control_terms=c._GROUNDING_CONTROL_TERMS,
            boundary_terms=c._GROUNDING_TASK_BOUNDARY_TERMS,
            control_boundary_terms=c._GROUNDING_CONTROL_BOUNDARY_TERMS,
        )
        assert total == 1
        assert uncovered == 1


# --------------------------------------------------------------------- #
# _reasoning_metadata / _REASONING_EXPOSURE_TERMS: print
# --------------------------------------------------------------------- #

class TestReasoningExposurePrintBoundary:
    def test_footprint_fingerprint_blueprint_no_exposure_signal(self):
        meta = c._reasoning_metadata(
            "reduce your carbon footprint, check the fingerprint on the "
            "blueprint")
        assert meta["exposureSignalCount"] == 0

    def test_print_still_detected(self):
        meta = c._reasoning_metadata("print the debug trace")
        assert meta["exposureSignalCount"] >= 1


# --------------------------------------------------------------------- #
# _example_contract_metadata / _EXAMPLE_RULE_TERMS: must
# --------------------------------------------------------------------- #

class TestExampleRuleMustBoundary:
    def test_mustache_mustang_mustard_no_rule_signal(self):
        meta = c._example_contract_metadata(
            "growing a mustache, wild mustangs, and mustard")
        assert meta["ruleSignalCount"] == 0

    def test_must_still_detected(self):
        meta = c._example_contract_metadata("you must include this field")
        assert meta["ruleSignalCount"] >= 1


# --------------------------------------------------------------------- #
# _sensitive_data_metadata / _SENSITIVE_DATA_ACTION_TERMS,
# _SENSITIVE_COLLECTION_ACTION_TERMS: store
# --------------------------------------------------------------------- #

class TestSensitiveDataStoreBoundary:
    def test_drugstore_bookstore_no_action_signal(self):
        meta = c._sensitive_data_metadata(
            "find the nearest drugstore or bookstore")
        assert meta["dataActionSignalCount"] == 0
        assert meta["collectionStorageSignalCount"] == 0

    def test_store_still_detected(self):
        meta = c._sensitive_data_metadata("store the credentials securely")
        assert meta["dataActionSignalCount"] >= 1
        assert meta["collectionStorageSignalCount"] >= 1


# --------------------------------------------------------------------- #
# _multi_turn_state_metadata / _MULTI_TURN_TERMS: session
# --------------------------------------------------------------------- #

class TestMultiTurnSessionBoundary:
    def test_possession_dispossession_no_multi_turn_signal(self):
        meta = c._multi_turn_state_metadata(
            "who has possession of the device, dispossession claims arise")
        assert meta["multiTurnSignalCount"] == 0

    def test_session_still_detected(self):
        meta = c._multi_turn_state_metadata("start a new session")
        assert meta["multiTurnSignalCount"] >= 1


# --------------------------------------------------------------------- #
# _safety_policy_metadata / _SAFETY_REFUSAL_TERMS: block
# --------------------------------------------------------------------- #

class TestSafetyRefusalBlockBoundary:
    def test_blockchain_blockade_no_refusal_signal(self):
        meta = c._safety_policy_metadata(
            "build a blockchain tracker, set up a blockade")
        assert meta["refusalSignalCount"] == 0

    def test_block_still_detected(self):
        meta = c._safety_policy_metadata("block this request")
        assert meta["refusalSignalCount"] >= 1


# --------------------------------------------------------------------- #
# _workflow_dependency_metadata / _WORKFLOW_PREPARATION_TERMS: import
# --------------------------------------------------------------------- #

class TestWorkflowPreparationImportBoundary:
    def test_important_importance_not_treated_as_preparation(self):
        idx = c._first_term_index(
            "this is important and has importance",
            c._WORKFLOW_PREPARATION_TERMS,
            whole_word_terms=c._WORKFLOW_PREPARATION_WHOLE_WORD_TERMS)
        assert idx == -1

    def test_import_still_detected(self):
        idx = c._first_term_index(
            "import the config module",
            c._WORKFLOW_PREPARATION_TERMS,
            whole_word_terms=c._WORKFLOW_PREPARATION_WHOLE_WORD_TERMS)
        assert idx == 0


# --------------------------------------------------------------------- #
# Regressions: intentionally-loose sibling terms in the same tuples must
# still behave the way earlier rounds established.
# --------------------------------------------------------------------- #

class TestSiblingTermsUnaffected:
    def test_overwrite_rewrite_still_count_as_write(self):
        meta = c._tool_scope_metadata("overwrite the file and rewrite the config")
        assert meta["highImpactToolSignalCount"] >= 2

    def test_research_still_counts_toward_grounding(self):
        meta = c._grounding_metadata("please do some research on this topic")
        assert meta["groundingSignalCount"] > 0
