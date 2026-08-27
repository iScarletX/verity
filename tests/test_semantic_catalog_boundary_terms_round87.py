"""Regression tests for the third wave of bare-term substring collisions
in semantic/catalog.py (Round 87).

Same bug class as test_semantic_catalog_boundary_terms.py (Round 82) and
test_semantic_catalog_boundary_terms_round83.py (Round 83): a
signal-detection tuple matches a bare short term as a plain substring, so
it false-positive-collides with an unrelated longer word that happens to
contain the same letters.

This round adds one new sub-pattern on top of the earlier suffix/prefix
collisions: the NEGATION-PREFIX ANTONYM collision, where the bare term is
the tail of its own negation-prefixed opposite ("continue" in
"discontinue", "approve" in "disapprove", "validate" in "invalidate",
"stop" in "nonstop", "publish" in "unpublish", "delete" in "undelete",
"inherit" in "disinherit", "violence" in "nonviolence", "escalate" in
"deescalate", "licensed" in "unlicensed", "mask" in "unmask", "authorized"
in "unauthorized"). An unprotected match here is worse than plain noise --
it asserts the opposite semantic signal from what the text actually says.

Each test reproduces one collision on a false-positive-only text (signal
suppressed) and a true-positive text (real signal still fires). No network
calls, no real model calls -- pure text-analysis helpers only.
"""
from __future__ import annotations

from verity.semantic import catalog as c


# --------------------------------------------------------------------- #
# _output_contract_metadata / _TYPE_TERMS, _ENUM_TERMS, _UNIT_TERMS
# --------------------------------------------------------------------- #

class TestOutputContractTypeBoundary:
    def test_hamstring_disarray_listen_objective_no_type_signal(self):
        meta = c._output_contract_metadata(
            "the hamstring injury caused disarray, then he began to "
            "listen, this seems quite objective.")
        assert meta["typeMarkerCount"] == 0

    def test_string_array_list_object_still_detected(self):
        meta = c._output_contract_metadata(
            "return a string type, use an array, wrap it in a list, "
            "output as object.")
        assert meta["typeMarkerCount"] > 0


class TestOutputContractEnumUnitBoundary:
    def test_enumerate_community_immunity_unity_no_signal(self):
        meta = c._output_contract_metadata(
            "lets enumerate the community and immunity issues, then "
            "discuss unity and remain united.")
        assert meta["enumMarkerCount"] == 0
        assert meta["unitMarkerCount"] == 0

    def test_enum_and_unit_still_detected(self):
        meta = c._output_contract_metadata(
            "choose an enum: allowed values are one of these, specify "
            "the unit and decimal format yyyy-mm-dd.")
        assert meta["enumMarkerCount"] > 0
        assert meta["unitMarkerCount"] > 0


# --------------------------------------------------------------------- #
# _select_conflict_candidate_lines / _STRONG_CONSTRAINT_MARKERS: never, only
# --------------------------------------------------------------------- #

class TestStrongConstraintNeverOnlyBoundary:
    def test_whenever_commonly_no_marker_hit(self):
        hit = c._any_term_hit(
            "whenever you commonly forget, remember to check.",
            c._STRONG_CONSTRAINT_MARKERS,
            boundary_terms=c._STRONG_CONSTRAINT_BOUNDARY_TERMS)
        assert hit is False

    def test_never_only_still_detected(self):
        hit = c._any_term_hit(
            "you must never do this, only do that.",
            c._STRONG_CONSTRAINT_MARKERS,
            boundary_terms=c._STRONG_CONSTRAINT_BOUNDARY_TERMS)
        assert hit is True


# --------------------------------------------------------------------- #
# _budget_metadata / _CONTINUATION_TERMS: continue (discontinue antonym)
# --------------------------------------------------------------------- #

class TestContinuationBoundary:
    def test_discontinue_no_continuation_signal(self):
        meta = c._budget_metadata(
            "we will discontinue this feature entirely.")
        assert meta["continuationSignalCount"] == 0

    def test_continue_still_detected(self):
        meta = c._budget_metadata(
            "please continue with the next response in the continuation.")
        assert meta["continuationSignalCount"] > 0


# --------------------------------------------------------------------- #
# _authority_metadata / _SIDE_EFFECT_TERMS: approve (disapprove antonym)
# --------------------------------------------------------------------- #

class TestSideEffectApproveBoundary:
    def test_disapprove_no_side_effect_signal(self):
        meta = c._authority_metadata(
            "the committee will disapprove of this proposal.")
        assert meta["sideEffectSignalCount"] == 0

    def test_approve_still_detected(self):
        meta = c._authority_metadata(
            "please approve the request and then transfer the funds.")
        assert meta["sideEffectSignalCount"] > 0


# --------------------------------------------------------------------- #
# _ambiguity_metadata / _VAGUE_CRITERIA_TERMS: appropriate, reasonable,
# sufficiently (inappropriate/unreasonable/insufficiently antonyms)
# --------------------------------------------------------------------- #

class TestVagueCriteriaAntonymBoundary:
    def test_inappropriate_unreasonable_insufficiently_no_signal(self):
        meta = c._ambiguity_metadata(
            "that is inappropriate to mention, an unreasonable ask, and "
            "insufficiently clear on its own.")
        assert meta["vagueCriterionCount"] == 0

    def test_appropriate_reasonable_sufficiently_still_detected(self):
        meta = c._ambiguity_metadata(
            "give an appropriate and reasonable response, sufficiently "
            "detailed.")
        assert meta["vagueCriterionCount"] > 0


# --------------------------------------------------------------------- #
# _verification_metadata / _VERIFICATION_TASK_TERMS: title, steps
# --------------------------------------------------------------------- #

class TestVerificationRequirementBoundary:
    def test_subtitle_entitled_footsteps_no_requirement_signal(self):
        meta = c._verification_metadata(
            "add a subtitle to the entitled book, watch your footsteps.")
        assert meta["requirementSignalCount"] == 0

    def test_title_steps_still_detected(self):
        meta = c._verification_metadata(
            "the title field, follow the steps carefully.")
        assert meta["requirementSignalCount"] > 0


# --------------------------------------------------------------------- #
# _verification_metadata / _VERIFICATION_CONTROL_TERMS: validate
# (invalidate antonym)
# --------------------------------------------------------------------- #

class TestVerificationControlValidateBoundary:
    def test_invalid_invalidate_no_verification_signal(self):
        meta = c._verification_metadata(
            "the treaty was ruled invalid, so we invalidate the contract.")
        assert meta["verificationSignalCount"] == 0

    def test_verify_validate_still_detected(self):
        meta = c._verification_metadata(
            "please verify and validate the output.")
        assert meta["verificationSignalCount"] > 0


# --------------------------------------------------------------------- #
# _verification_metadata / _DOWNSTREAM_TERMS: production, decision
# (reproduction/indecision antonyms)
# --------------------------------------------------------------------- #

class TestDownstreamProductionDecisionBoundary:
    def test_indecision_reproduction_no_downstream_signal(self):
        meta = c._verification_metadata(
            "there was some indecision about the reproduction of the "
            "artwork.")
        assert meta["downstreamSignalCount"] == 0

    def test_production_decision_still_detected(self):
        meta = c._verification_metadata(
            "this output feeds a downstream parser used in production "
            "for automated decisions.")
        assert meta["downstreamSignalCount"] > 0


# --------------------------------------------------------------------- #
# _input_contract_metadata / _INPUT_HANDLING_TERMS: validate
# (invalidate antonym)
# --------------------------------------------------------------------- #

class TestInputHandlingValidateBoundary:
    def test_invalidate_no_handling_signal(self):
        meta = c._input_contract_metadata(
            "this action would invalidate the entire response.")
        assert meta["handlingSignalCount"] == 0

    def test_validate_still_detected(self):
        meta = c._input_contract_metadata(
            "please validate and normalize the input, return an error "
            "if malformed.")
        assert meta["handlingSignalCount"] > 0


# --------------------------------------------------------------------- #
# _example_contract_metadata / _EXAMPLE_RULE_TERMS: must, enum, never,
# field
# --------------------------------------------------------------------- #

class TestExampleRuleBoundary:
    def test_mustache_mustang_mustard_enumerate_whenever_battlefield_no_signal(
            self):
        meta = c._example_contract_metadata(
            "he wore a mustache, rode into the mustang ranch eating "
            "mustard, and will enumerate outside whenever it is needed, "
            "guarding the battlefield.")
        assert meta["ruleSignalCount"] == 0

    def test_must_field_enum_never_still_detected(self):
        meta = c._example_contract_metadata(
            "the response must include a field called status which must "
            "be one of enum values, and should never be empty.")
        assert meta["ruleSignalCount"] > 0


# --------------------------------------------------------------------- #
# _sensitive_data_metadata / _SENSITIVE_DATA_CONTROL_TERMS: mask,
# authorized (unmask/unauthorized antonyms)
# --------------------------------------------------------------------- #

class TestSensitiveDataControlAntonymBoundary:
    def test_unmask_unauthorized_no_control_signal(self):
        meta = c._sensitive_data_metadata(
            "the thief tried to unmask the hero, having remained "
            "completely unauthorized this whole time.")
        assert meta["dataControlSignalCount"] == 0

    def test_mask_authorized_still_detected(self):
        meta = c._sensitive_data_metadata(
            "please mask the credit card number and ensure the requester "
            "is authorized before granting access control.")
        assert meta["dataControlSignalCount"] > 0


# --------------------------------------------------------------------- #
# _field_constraint_metadata / _FIELD_CONTRACT_TERMS, _FIELD_TYPE_TERMS,
# _FIELD_UNIT_PRECISION_TERMS: amount, currency, precision, type
# (distinct from the object/list/unit/range/date collisions already
# covered by TestFieldConstraintBoundary in round83)
# --------------------------------------------------------------------- #

class TestFieldConstraintAmountCurrencyPrecisionTypeBoundary:
    def test_tantamount_concurrency_imprecision_prototype_typical_no_signal(
            self):
        meta = c._field_constraint_metadata(
            "this is a tantamount concern, there is concurrency in the "
            "system, some imprecision in the estimate, this is a "
            "prototype and quite typical.")
        assert meta["fieldSignalCount"] == 0
        assert meta["unitPrecisionSignalCount"] == 0
        assert meta["fieldTypeSignalCount"] == 0

    def test_amount_currency_precision_type_still_detected(self):
        meta = c._field_constraint_metadata(
            "the amount field, set the currency to USD, specify the "
            "precision, this is a string type.")
        assert meta["fieldSignalCount"] > 0
        assert meta["unitPrecisionSignalCount"] > 0
        assert meta["fieldTypeSignalCount"] > 0


# --------------------------------------------------------------------- #
# _workflow_dependency_metadata / _WORKFLOW_BRANCH_TERMS: stop (nonstop
# antonym)
# --------------------------------------------------------------------- #

class TestWorkflowBranchStopBoundary:
    def test_nonstop_no_branch_signal(self):
        meta = c._workflow_dependency_metadata(
            "the train made a nonstop journey.")
        assert meta["workflowBranchSignalCount"] == 0

    def test_stop_still_detected(self):
        meta = c._workflow_dependency_metadata(
            "otherwise, stop the process if it fails.")
        assert meta["workflowBranchSignalCount"] > 0


# --------------------------------------------------------------------- #
# _WORKFLOW_SIDE_EFFECT_TERMS: publish, delete (unpublish/undelete
# antonyms); _WORKFLOW_VALIDATION_TERMS: test, tests, validate (latest/
# contest collisions, invalidate antonym)
# --------------------------------------------------------------------- #

class TestWorkflowSideEffectValidationBoundary:
    def test_unpublish_undelete_latest_contest_invalidate_no_signal(self):
        fp = ("please do not unpublish or undelete anything, this is "
              "the latest contest and it will invalidate results.")
        side_effect = c._sum_term_hits(
            fp, c._WORKFLOW_SIDE_EFFECT_TERMS,
            boundary_terms=c._WORKFLOW_SIDE_EFFECT_BOUNDARY_TERMS)
        validation = c._sum_term_hits(
            fp, c._WORKFLOW_VALIDATION_TERMS,
            boundary_terms=c._WORKFLOW_VALIDATION_BOUNDARY_TERMS)
        assert side_effect == 0
        assert validation == 0

    def test_publish_delete_test_validate_still_detected(self):
        tp = ("first publish the results, then delete the temp files, "
              "run the acceptance tests to validate.")
        side_effect = c._sum_term_hits(
            tp, c._WORKFLOW_SIDE_EFFECT_TERMS,
            boundary_terms=c._WORKFLOW_SIDE_EFFECT_BOUNDARY_TERMS)
        validation = c._sum_term_hits(
            tp, c._WORKFLOW_VALIDATION_TERMS,
            boundary_terms=c._WORKFLOW_VALIDATION_BOUNDARY_TERMS)
        assert side_effect > 0
        assert validation > 0


# --------------------------------------------------------------------- #
# _attention_dilution_metadata / _ATTENTION_REPETITION_TERMS: again
# (against collision)
# --------------------------------------------------------------------- #

class TestAttentionRepetitionAgainBoundary:
    def test_against_no_repetition_signal(self):
        meta = c._attention_dilution_metadata(
            "we stand against this policy, against all odds.")
        assert meta["repetitionSignalCount"] == 0

    def test_again_still_detected(self):
        meta = c._attention_dilution_metadata(
            "please repeat this again, the instructions are duplicated "
            "again.")
        assert meta["repetitionSignalCount"] > 0


# --------------------------------------------------------------------- #
# _multi_turn_state_metadata / _STATE_INHERITANCE_TERMS: inherit
# (disinherit antonym); _STATE_RESET_TERMS: reset (preset collision)
# --------------------------------------------------------------------- #

class TestStateInheritanceResetBoundary:
    def test_disinherit_preset_no_signal(self):
        meta = c._multi_turn_state_metadata(
            "the will explicitly disinherit the nephew, using a preset "
            "configuration.")
        assert meta["stateInheritanceSignalCount"] == 0
        assert meta["stateResetSignalCount"] == 0

    def test_inherit_reset_still_detected(self):
        meta = c._multi_turn_state_metadata(
            "please inherit the previous context and reset the state "
            "for a new session.")
        assert meta["stateInheritanceSignalCount"] > 0
        assert meta["stateResetSignalCount"] > 0


# --------------------------------------------------------------------- #
# _safety_policy_metadata / _SAFETY_DOMAIN_TERMS: violence (nonviolence
# antonym); _SAFETY_ESCALATION_TERMS: escalate (deescalate antonym)
# --------------------------------------------------------------------- #

class TestSafetyDomainEscalationBoundary:
    def test_nonviolence_deescalate_no_signal(self):
        meta = c._safety_policy_metadata(
            "the protest emphasized nonviolence and sought to deescalate "
            "tensions.")
        assert meta["safetyDomainSignalCount"] == 0
        assert meta["escalationSignalCount"] == 0

    def test_violence_escalate_still_detected(self):
        meta = c._safety_policy_metadata(
            "this involves violence and weapons, please escalate to "
            "human review.")
        assert meta["safetyDomainSignalCount"] > 0
        assert meta["escalationSignalCount"] > 0


# --------------------------------------------------------------------- #
# _source_use_policy_metadata / _SOURCE_ATTRIBUTION_TERMS: credit,
# citation (discredit/recitation collisions); _SOURCE_LIMIT_TERMS,
# _SOURCE_USE_TERMS: licensed (unlicensed antonym, book whole-word)
# --------------------------------------------------------------------- #

class TestSourceAttributionLimitUseBoundary:
    def test_discredit_recitation_unlicensed_bookkeeping_no_signal(self):
        meta = c._source_use_policy_metadata(
            "this will discredit the source, it is just a recitation "
            "from memory, the software is unlicensed, and he works in "
            "bookkeeping.")
        assert meta["attributionSignalCount"] == 0
        assert meta["sourceLimitSignalCount"] == 0
        assert meta["sourceUseSignalCount"] == 0

    def test_credit_citation_licensed_book_still_detected(self):
        meta = c._source_use_policy_metadata(
            "please cite this book with proper attribution and credit, "
            "note the citation, and confirm it is licensed under a "
            "public license.")
        assert meta["attributionSignalCount"] > 0
        assert meta["sourceLimitSignalCount"] > 0
        assert meta["sourceUseSignalCount"] > 0


# --------------------------------------------------------------------- #
# _declared_behavior_families / _WHOLE_WORD_BARE_BEHAVIOR_TERMS: shell,
# secret (shellfish/secretary/secretive/secretly collisions)
# --------------------------------------------------------------------- #

class TestDeclaredBehaviorShellSecretBoundary:
    def test_shellfish_secretary_secretive_secretly_no_family(self):
        declared, denied = c._declared_behavior_families(
            "we sell shellfish for a living and the new secretary keeps "
            "things secretive, working secretly.")
        assert "process_execution" not in declared
        assert "process_execution" not in denied
        assert "credential_access" not in declared
        assert "credential_access" not in denied

    def test_shell_secret_still_detected(self):
        declared, denied = c._declared_behavior_families(
            "run this shell command and keep the secret credential safe.")
        assert "process_execution" in declared
        assert "credential_access" in declared


# --------------------------------------------------------------------- #
# Regressions: intentionally-loose sibling terms in the same tuples must
# still behave the way earlier rounds established.
# --------------------------------------------------------------------- #

class TestSiblingTermsUnaffected:
    def test_disapprove_does_not_suppress_reject(self):
        meta = c._authority_metadata(
            "the manager will disapprove or reject the change request.")
        assert meta["sideEffectSignalCount"] > 0

    def test_licensed_book_singular_and_plural_both_count(self):
        meta = c._source_use_policy_metadata(
            "this book and that book are both licensed for reuse.")
        assert meta["sourceUseSignalCount"] >= 2
