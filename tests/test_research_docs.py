"""Governance checks for strategy research documentation."""

from pathlib import Path

import yaml

from core.registry import discover_strategies


def test_enabled_strategies_have_research_metadata_and_doc():
    """Every enabled strategy must declare a research doc that exists in repo."""
    repo_root = Path(__file__).resolve().parents[1]
    manifests = discover_strategies(repo_root / "strategies")

    assert manifests, "No enabled strategies found for research-doc validation."

    for manifest in manifests:
        data = yaml.safe_load(manifest.manifest_path.read_text())
        research = data.get("research")

        assert isinstance(research, dict), f"{manifest.id} missing `research` block in manifest."

        required_keys = ["paper_title", "website_path", "canonical_doc"]
        missing = [k for k in required_keys if not research.get(k)]
        assert not missing, f"{manifest.id} research block missing required keys: {missing}"

        canonical_doc = repo_root / research["canonical_doc"]
        assert canonical_doc.exists(), (
            f"{manifest.id} canonical research doc not found: {research['canonical_doc']}"
        )

        website_path = research["website_path"]
        assert website_path.startswith("/"), (
            f"{manifest.id} research.website_path must start with '/': {website_path}"
        )

        source_docx = research.get("source_docx")
        if source_docx:
            source_path = repo_root / source_docx
            assert source_path.exists(), (
                f"{manifest.id} source DOCX not found: {source_docx}"
            )
            assert source_path.suffix.lower() == ".docx", (
                f"{manifest.id} source_docx must point to a .docx file: {source_docx}"
            )

        external_url = research.get("external_doc_url")
        if external_url:
            assert external_url.startswith("https://"), (
                f"{manifest.id} research.external_doc_url must use https: {external_url}"
            )
