"""Same rule + same subject hitting multiple locations must render as one
merged card in the Web findingsDisplay list, not one card per occurrence.

User-reported confusion: identical near-duplicate cards for the same
underlying issue found at several places in the source. The raw scored
``findings`` list is untouched (score/SARIF/history keep one Finding per
occurrence); only the Web display projection merges evidence locations.
"""
from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.web.view import build_view_model


def review(text):
    snap, data = intake_text(text)
    return run_review(ReviewInputs("prompt", snap, data))


def test_repeated_same_placeholder_merges_into_one_display_card_with_all_hits():
    r = review("Use {{ unfinished }} before {{ unfinished }} responding.")
    report = review_to_dict(r)
    raw_type_count = sum(
        1 for f in report["findings"]
        if f["findingType"] == "prompt.unfilled_placeholder")
    assert raw_type_count == 2  # scoring/SARIF/history keep both occurrences

    view = build_view_model(report, "rid")
    assert len(view["findings"]) == len(report["findings"])  # untouched

    display = [f for f in view["findingsDisplay"]
               if f["type"] == "prompt.unfilled_placeholder"]
    assert len(display) == 1
    assert display[0]["hitCount"] == 2
    assert len(display[0]["evidences"]) == 2


def test_distinct_subjects_are_not_merged():
    r = review("See section 7 for details. See section 9 for more.")
    report = review_to_dict(r)
    view = build_view_model(report, "rid")
    display = [f for f in view["findingsDisplay"]
               if f["type"] == "prompt.dangling_section_reference"]
    assert len(display) == 2  # different referenceText -> different subjectKey
    assert all(f["hitCount"] == 1 for f in display)


def test_single_occurrence_has_no_hit_count_badge_worthy_value():
    r = review("Use {{ unfinished }} once.")
    report = review_to_dict(r)
    view = build_view_model(report, "rid")
    display = [f for f in view["findingsDisplay"]
               if f["type"] == "prompt.unfilled_placeholder"]
    assert len(display) == 1
    assert display[0]["hitCount"] == 1
