import io
import json
import zipfile

from backend.app.artifacts import validate_artifacts_with_report
from backend.app.open_design import normalize_open_design_metadata, open_design_bundle_for_artifact


def test_normalize_open_design_metadata_allows_supported_material_types_only():
    metadata = normalize_open_design_metadata(
        {
            "material_type": "DESIGN_BRIEF",
            "skill_name": "<script>Bad</script>",
            "audience": "Product",
            "unknown": "ignored",
        }
    )

    assert metadata["material_type"] == "design_brief"
    assert metadata["skill_name"] == "bad"
    assert metadata["audience"] == "Product"
    assert "unknown" not in metadata

    fallback = normalize_open_design_metadata({"material_type": "html_preview"})
    assert fallback["material_type"] == "docs_page"


def test_open_design_bundle_contains_skill_design_content_and_metadata():
    row = {
        "id": "art_1",
        "title": "Launch Brief",
        "caption": "Grounded in uploaded files",
        "kind": "file_draft",
        "source_chunk_ids": ["chk_1"],
    }
    spec = {
        "filename": "launch.md",
        "format": "markdown",
        "content": "# Launch\n\nUse source facts only.",
        "open_design": {"material_type": "design_brief", "skill_name": "FileChat Design Doc"},
    }

    body, filename = open_design_bundle_for_artifact(row, spec)

    assert filename == "launch-brief-open-design.zip"
    with zipfile.ZipFile(io.BytesIO(body)) as bundle:
        assert bundle.namelist() == ["SKILL.md", "DESIGN.md", "content.md", "metadata.json"]
        skill = bundle.read("SKILL.md").decode("utf-8")
        design = bundle.read("DESIGN.md").decode("utf-8")
        content = bundle.read("content.md").decode("utf-8")
        metadata = json.loads(bundle.read("metadata.json"))

    assert "name: FileChat Design Doc" in skill
    assert "## 1. Purpose" in design
    assert content == "# Launch\n\nUse source facts only."
    assert metadata["open_design_compatible"] is True
    assert metadata["material_type"] == "design_brief"
    assert metadata["source_artifact_id"] == "art_1"
    assert metadata["source_chunk_ids"] == ["chk_1"]


def test_file_draft_validation_preserves_open_design_metadata_without_html_rendering():
    report = validate_artifacts_with_report(
        [
            {
                "kind": "file_draft",
                "title": "Design brief",
                "source_ids": [1],
                "filename": "brief.md",
                "format": "markdown",
                "content": "# Brief",
                "open_design": {"material_type": "one_pager", "html": "<script>alert(1)</script>"},
            }
        ],
        [{"source_id": 1, "chunk_id": "chk_1"}],
    )

    assert report.warnings == []
    spec = report.artifacts[0].spec
    assert spec["open_design"]["material_type"] == "one_pager"
    assert "html" not in spec["open_design"]
