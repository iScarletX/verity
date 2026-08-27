from pathlib import Path

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.web.view import build_view_model


ROOT = Path(__file__).parent.parent


def _art_report():
    snapshot, file_bytes = intake_text(
        "水墨画风转换器：保持主体，输出 positive_prompt 和 negative_prompt。"
    )
    return review_to_dict(run_review(ReviewInputs(
        engine="prompt", snapshot=snapshot, file_bytes=file_bytes,
    )))


def test_web_view_projects_dynamic_selection_and_unavailable_counts():
    view = build_view_model(_art_report(), "review-art")

    assert view["behaviorProfile"]["domain_tags"] == ["art_style"]
    assert view["dynamicPlan"]["counts"]["selected"] > 0
    assert view["dynamicPlan"]["counts"]["unavailable"] == 1
    visual = next(
        item for item in view["dynamicPlan"]["items"]
        if item["checkId"] == "art_style.visual_fidelity"
    )
    assert visual["status"] == "unavailable"
    assert visual["reasonCodes"] == ["image_runtime_not_configured"]


def test_web_assets_place_dynamic_coverage_before_raw_findings():
    index = (ROOT / "src/verity/web/static/index.html").read_text()
    script = (ROOT / "src/verity/web/static/app.js").read_text()

    assert index.index('id="unified-issues"') < index.index('id="findings"')
    assert index.index('id="dynamic-plan"') < index.index('id="findings"')
    assert "function renderUnifiedIssues" in script
    assert "function renderDynamicPlan" in script
    assert "不适用的检查" in script
