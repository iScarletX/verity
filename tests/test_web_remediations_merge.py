"""Same rule + same subject hitting multiple locations must render as one
merged remediation in the Web "整改与复查" checklist, not one per occurrence.

Mirrors tests/test_web_findings_merge.py but for view["remediations"]
(sourced from scoring.py::build_remediations, one dict per scored Finding).
The underlying report["remediations"] list is untouched -- only the Web
view-model projection merges same-subject entries.
"""
from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.web.view import build_view_model


def review(text):
    snap, data = intake_text(text)
    return run_review(ReviewInputs("prompt", snap, data))


def test_repeated_same_placeholder_merges_into_one_remediation_with_all_evidence():
    r = review("Use {{ unfinished }} before {{ unfinished }} responding.")
    report = review_to_dict(r)
    raw_rem_count = sum(
        1 for x in report["remediations"]
        if x["findingType"] == "prompt.unfilled_placeholder")
    assert raw_rem_count == 2  # source keeps one remediation per occurrence

    view = build_view_model(report, "rid")
    rems = [x for x in view["remediations"]]
    assert len(rems) == 1
    assert rems[0]["hitCount"] == 2


def test_distinct_subjects_are_not_merged_in_remediations():
    r = review("See section 7 for details. See section 9 for more.")
    report = review_to_dict(r)
    view = build_view_model(report, "rid")
    rems = [x for x in view["remediations"]
            if "section" in (x.get("title") or "").lower()
            or x.get("hitCount")]
    # Both dangling-section-reference remediations must stay distinct.
    matching_ids = [
        f["findingId"] for f in report["findings"]
        if f["findingType"] == "prompt.dangling_section_reference"]
    assert len(matching_ids) == 2
    all_rems = view["remediations"]
    assert all(r["hitCount"] == 1 for r in all_rems
               if r.get("findingId") in matching_ids)
