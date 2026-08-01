"""Presentation checks for collapsible adjusted-percentage columns."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
TEMPLATES = (
    "world_stage/templates/year/summary.html",
    "world_stage/templates/revote/results.html",
    "world_stage/templates/year/index.html",
    "world_stage/templates/year/specials.html",
)


def test_adjusted_percentage_pages_collapse_the_detail_column_by_default():
    for relative_path in TEMPLATES:
        source = (ROOT / relative_path).read_text()

        assert source.count("Collapse detail columns") == 1
        assert 'type="checkbox" checked' in source
        assert "adjusted-percent-table" in source
        assert "hide-detail" in source
        assert '<th class="detail-col"' in source
        assert "percent detail-col" in source
        assert "classList.toggle('hide-detail', this.checked)" in source


def test_global_table_style_hides_collapsed_detail_cells():
    stylesheet = (ROOT / "world_stage/static/css/index.css").read_text()

    assert "table.hide-detail .detail-col" in stylesheet
    assert "display: none;" in stylesheet.split(
        "table.hide-detail .detail-col", 1
    )[1].split("}", 1)[0]
