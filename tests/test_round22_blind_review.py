"""Round 22 blind dual-AI Corpus review containment."""
from copy import deepcopy

import pytest

from verity.blind_review import (build_blind_packet,
                                 compare_blind_reviews,
                                 validate_review_result)
from verity.corpus import CorpusError, load_manifest
from verity.review_evidence import (load_independent_ai_attestation,
                                    require_independent_ai_case,
                                    ReviewEvidenceError)
from verity.semantic.catalog import extract_external_instruction_trust_gap
from verity.semantic_quality import (evaluate_semantic_model_quality,
                                     load_semantic_quality_manifest)


SEED_A = "reviewer-a-fixed-test-seed-0123456789"
SEED_B = "reviewer-b-fixed-test-seed-9876543210"


def packets():
    a, ma = build_blind_packet(reviewer_id="reviewer-a", seed=SEED_A)
    b, mb = build_blind_packet(reviewer_id="reviewer-b", seed=SEED_B)
    return a, ma, b, mb


def result_for(packet, decision="absent"):
    return {
        "schemaVersion": 1,
        "protocolVersion": "1.0.0",
        "packetReviewerId": packet["reviewerId"],
        "decisions": [{
            "itemId": item["itemId"],
            "decision": decision,
            "confidence": 0.8,
            "evidence": "Artifact file contains the cited bounded behavior.",
            "reason": "Decision follows the supplied target-risk definition.",
        } for item in packet["items"]],
    }


def test_packets_have_54_nonsealed_items_and_different_alias_order():
    a, ma, b, mb = packets()
    assert a["itemCount"] == b["itemCount"] == 54
    assert len(ma["aliases"]) == len(mb["aliases"]) == 54
    order_a = [ma["aliases"][x["itemId"]]["canonicalId"] for x in a["items"]]
    order_b = [mb["aliases"][x["itemId"]]["canonicalId"] for x in b["items"]]
    assert order_a != order_b
    assert set(order_a) == set(order_b)
    assert all("semantic-quality-test" not in cid for cid in order_a)
    assert all("sealed" not in cid for cid in order_a)
    assert set(ma["aliases"]) != set(mb["aliases"])


def test_packet_structured_data_leaks_no_answers_or_original_identifiers():
    a, ma, _, _ = packets()
    text = __import__("json").dumps(a, ensure_ascii=False)
    for forbidden in (
        '"caseId"', '"riskId"', '"authorDecision"', '"rationale"',
        '"expectedRiskIds"', '"expectedAssessment"', '"expectedSeverity"',
        '"labelStatus"', '"split"', '"findingType"',
        "semantic-quality-selection", "cases/", "semantic-cases/",
    ):
        assert forbidden not in text
    # Reversible facts stay only in the private map.
    assert any(x["canonicalId"].startswith("l0:")
               for x in ma["aliases"].values())
    assert "authorDecision" in next(iter(ma["aliases"].values()))


def test_skill_identity_is_anonymized_while_match_mismatch_invalid_shape_remains():
    a, ma, _, _ = packets()
    by_canonical = {meta["canonicalId"]: alias
                    for alias, meta in ma["aliases"].items()}
    by_alias = {x["itemId"]: x for x in a["items"]}

    valid_alias = by_canonical["l0:skill-name-syntax-safe"]
    valid = by_alias[valid_alias]
    assert valid["artifact"]["displayRootName"].startswith("review-skill-")
    valid_skill = next(x["content"] for x in valid["artifact"]["files"]
                       if x["path"] == "SKILL.md")
    assert ("name: " + valid["artifact"]["displayRootName"]) in valid_skill
    assert "skill-name-syntax-safe" not in valid_skill

    mismatch_alias = by_canonical["l0:skill-directory-mismatch-positive"]
    mismatch = by_alias[mismatch_alias]
    mismatch_skill = next(x["content"] for x in mismatch["artifact"]["files"]
                          if x["path"] == "SKILL.md")
    assert ("name: " + mismatch["artifact"]["displayRootName"]) not in mismatch_skill
    assert "different-directory-name" not in mismatch_skill

    invalid_alias = by_canonical["l0:skill-name-syntax-positive"]
    invalid = by_alias[invalid_alias]
    invalid_skill = next(x["content"] for x in invalid["artifact"]["files"]
                         if x["path"] == "SKILL.md")
    assert "name: Invalid_Name_" in invalid_skill
    assert "Invalid_Name" not in invalid["artifact"]["displayRootName"]


def test_packet_content_is_deterministic_for_same_seed():
    one, map_one = build_blind_packet(reviewer_id="a", seed=SEED_A)
    two, map_two = build_blind_packet(reviewer_id="a", seed=SEED_A)
    assert one == two
    assert map_one == map_two


def test_result_validation_rejects_missing_duplicate_bad_and_overlong():
    a, _, _, _ = packets()
    valid = result_for(a)
    assert validate_review_result(valid, a) is valid

    missing = deepcopy(valid); missing["decisions"].pop()
    with pytest.raises(CorpusError, match="incomplete"):
        validate_review_result(missing, a)

    duplicate = deepcopy(valid)
    duplicate["decisions"][-1]["itemId"] = duplicate["decisions"][0]["itemId"]
    with pytest.raises(CorpusError, match="invalid/duplicate"):
        validate_review_result(duplicate, a)

    bad = deepcopy(valid); bad["decisions"][0]["decision"] = "looks_safe"
    with pytest.raises(CorpusError, match="enum"):
        validate_review_result(bad, a)

    long_reason = deepcopy(valid); long_reason["decisions"][0]["reason"] = "x" * 501
    with pytest.raises(CorpusError, match="reason"):
        validate_review_result(long_reason, a)


def test_comparison_never_auto_upgrades_disagreement_or_uncertain():
    a, ma, b, mb = packets()
    ra = result_for(a)
    rb = result_for(b)
    # Set both reviewers to the author answer through their private maps so all
    # 54 first qualify, then perturb two canonical cases.
    for row in ra["decisions"]:
        row["decision"] = ma["aliases"][row["itemId"]]["authorDecision"]
    for row in rb["decisions"]:
        row["decision"] = mb["aliases"][row["itemId"]]["authorDecision"]
    target_uncertain = ma["aliases"][ra["decisions"][0]["itemId"]]["canonicalId"]
    ra["decisions"][0]["decision"] = "uncertain"
    target_disagree = ma["aliases"][ra["decisions"][1]["itemId"]]["canonicalId"]
    b_alias = next(alias for alias, meta in mb["aliases"].items()
                   if meta["canonicalId"] == target_disagree)
    b_row = next(row for row in rb["decisions"] if row["itemId"] == b_alias)
    b_row["decision"] = ("absent" if b_row["decision"] == "present" else "present")

    report = compare_blind_reviews(
        packet_a=a, map_a=ma, result_a=ra,
        packet_b=b, map_b=mb, result_b=rb)
    assert report["sealedTestReviewed"] is False
    assert report["reviewClass"] == "independent_ai_review"
    assert report["counts"] == {
        "unanimousMatchAuthor": 52,
        "unanimousDisagreeAuthor": 0,
        "reviewerDisagreement": 1,
        "uncertain": 1,
    }
    assert report["candidateForIndependentAiReviewCount"] == 52
    statuses = {x["canonicalId"]: x["status"] for x in report["details"]}
    assert statuses[target_uncertain] == "uncertain"
    assert statuses[target_disagree] == "reviewerDisagreement"


def test_committed_attestation_binds_exactly_54_nonsealed_current_payloads():
    """The frozen Round-22 attestation covers exactly the 54 cases that were
    actually reviewed (26 L0 + 28 non-Test semantic). Round 31 added two new
    L0 cases (VR-PROMPT-008) as ``provisional_single_review`` -- they are
    correctly NOT part of this frozen attestation; a future review round
    would need to explicitly cover them, which must not happen silently.
    """
    attestation = load_independent_ai_attestation()
    l0 = load_manifest()
    semantic = load_semantic_quality_manifest()
    assert len(attestation) == 54
    l0_reviewed = {c["caseId"] for c in l0["cases"]
                  if c["labelStatus"] == "independent_ai_review"}
    l0_provisional = {c["caseId"] for c in l0["cases"]
                      if c["labelStatus"] == "provisional_single_review"}
    assert len(l0_reviewed) == 26
    assert l0_provisional == {"prompt-untrusted-input-boundary-positive",
                              "prompt-untrusted-input-boundary-safe",
                              "skill-sensitive-path-positive",
                              "skill-sensitive-path-safe",
                              "prompt-dangling-reference-positive",
                              "prompt-dangling-reference-safe",
                              "skill-tls-verification-positive",
                              "skill-tls-verification-safe",
                              "prompt-secret-positive",
                              "prompt-secret-safe",
                              "skill-credential-positive",
                              "skill-credential-safe",
                              "skill-external-instructions-positive",
                              "skill-external-instructions-safe",
                              "skill-deserialization-positive",
                              "skill-deserialization-safe",
                              "skill-network-destination-positive",
                              "skill-network-destination-safe",
                              "skill-output-rendering-positive",
                              "skill-output-rendering-safe"}
    assert {c["labelStatus"] for c in semantic["cases"]
            if c["split"] != "test"} == {"independent_ai_review"}
    assert {c["labelStatus"] for c in semantic["cases"]
            if c["split"] == "test"} == {"provisional_single_review"}
    expected_ids = set(l0_reviewed)
    expected_ids |= {c["caseId"] for c in semantic["cases"]
                     if c["split"] != "test"}
    assert set(attestation) == expected_ids
    assert not (l0_provisional & set(attestation))
    assert not ({c["caseId"] for c in semantic["cases"]
                 if c["split"] == "test"} & set(attestation))


def test_attestation_rejects_stale_digest_or_wrong_decision():
    attestation = load_independent_ai_attestation()
    case_id = "semantic-external-trust-safe"
    item = attestation[case_id]
    with pytest.raises(ReviewEvidenceError, match="missing/stale"):
        require_independent_ai_case(
            case_id=case_id, source_class=item["sourceClass"],
            payload_digest="0" * 64, expected_decision="absent",
            attestation=attestation)
    with pytest.raises(ReviewEvidenceError, match="missing/stale"):
        require_independent_ai_case(
            case_id=case_id, source_class=item["sourceClass"],
            payload_digest=item["payloadDigest"], expected_decision="present",
            attestation=attestation)


def test_data_only_external_reference_is_neutral_seed_not_l0_instruction():
    from pathlib import Path
    from verity.intake import intake_directory
    from verity.report import review_to_dict
    from verity.review import ReviewInputs, run_review
    root = Path(__file__).resolve().parents[1]
    path = root / "evals/corpus/v1/semantic-cases/external-trust-safe"
    snapshot, file_bytes = intake_directory(path)
    review = run_review(ReviewInputs("skill", snapshot, file_bytes,
                                     profile="minimal"))
    manifest = review.artifactModel["manifest"]
    assert manifest["external_reference_count"] == 1
    assert manifest["external_instruction_urls"] == []
    assert all(f.findingType != "skill.external_instruction_reference"
               for f in review.findings)
    seeds = extract_external_instruction_trust_gap(
        review_to_dict(review), file_bytes)
    assert len(seeds) == 1
    # Neutral seed metadata/count must not expose the URL in reports.
    assert "selection-reference" not in __import__("json").dumps(
        review_to_dict(review), ensure_ascii=False)


def test_protocol_v1_selection_is_invalidated_and_v2_fingerprints_corpus():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    record = json.loads((root / "evals/reviews/semantic-selection-v1-invalidation.json").read_text())
    assert record["status"] == "invalidated_by_label_adjudication"
    assert record["protocolVersion"] == "1.0.0"
    assert record["replacementProtocolVersion"] == "2.0.0"
    assert record["sealedTestConsumed"] is False
    assert "configuration_fingerprint_v1_omitted_corpus_digest" in record["reasonCodes"]
    manifest = load_semantic_quality_manifest()
    assert manifest["protocolVersion"] == "2.0.0"
    # The old v1 configuration cannot be mistaken for the v2 configuration:
    # v2 report generation exposes and fingerprints the selected Corpus bytes.
    from verity.semantic_quality import _config_fingerprint
    from verity.semantic.config import ProviderConfig, ProviderCredentials
    cred = ProviderCredentials("UNSET_TEST_ONLY")
    gen = ProviderConfig(role="candidate_generator", provider_id="p",
                         model_id="m", base_url="https://example.invalid/v1",
                         credentials=cred)
    val = ProviderConfig(role="validator", provider_id="p", model_id="m",
                         base_url="https://example.invalid/v1", credentials=cred)
    a = _config_fingerprint(gen, val, temperature=0, max_output_tokens=800,
                            repetitions=2, role_prompt_version="2.0.0",
                            protocol_version="2.0.0", corpus_fingerprint="a" * 64)
    b = _config_fingerprint(gen, val, temperature=0, max_output_tokens=800,
                            repetitions=2, role_prompt_version="2.0.0",
                            protocol_version="2.0.0", corpus_fingerprint="b" * 64)
    assert a != b
