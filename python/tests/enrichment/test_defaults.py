"""task.w2.enrichment — packaged defaults load and stay whole.

The defaults package carries the cms instance's declarative/config
data (okg-deployments main@f33a9c4) plus the wisdqm extraction extras;
these tests pin the counts and shapes so a broken copy or a packaging
regression fails loudly.
"""
import re

import pytest

from archi.enrichment import defaults
from archi.enrichment.declarative import GlobalTagReleaseLinker


def test_load_all_loads_every_default():
    loaded = defaults.load_all()
    assert set(loaded) == {
        "extraction_rules",
        "extraction_rules_dqm",
        "identifier_patterns",
        "linker_config",
        "cross_links",
    }
    assert len(loaded["extraction_rules"]) == 8
    assert len(loaded["extraction_rules_dqm"]) == 3
    assert len(loaded["identifier_patterns"]) == 8
    assert len(loaded["linker_config"]["routes"]) == 8
    assert set(loaded["cross_links"]) == {"global_tag_release.yaml"}


def test_extraction_rules_shape_and_regexes():
    rules = defaults.load_extraction_rules()
    by_type = {id_type: (regex, conf) for id_type, regex, conf in rules}
    assert set(by_type) == {
        "cms_site",
        "cms_jira_key",
        "cmssw_release",
        "cms_hostname",
        "global_tag",
        "cms_dataset",
        "cms_run_number",
        "cms_workflow",
    }
    site_regex, site_conf = by_type["cms_site"]
    assert site_conf == 0.95
    assert re.search(site_regex, "failures at T2_US_MIT today").group(1) == "T2_US_MIT"
    assert re.search(by_type["cms_jira_key"][0], "see CMSCOMPPR-67026").group(1) == "CMSCOMPPR-67026"


def test_dqm_extraction_rules_are_the_wisdqm_extras():
    rules = defaults.load_dqm_extraction_rules()
    by_type = {id_type: regex for id_type, regex, _ in rules}
    assert set(by_type) == {"hlt_path", "l1t_seed", "dqm_workspace"}
    assert re.search(by_type["hlt_path"], "path HLT_Mu50_v3 fired").group(1) == "HLT_Mu50_v3"
    assert re.search(by_type["l1t_seed"], "seed L1_SingleMu22").group(1) == "L1_SingleMu22"
    assert re.search(by_type["dqm_workspace"], "check TKDQM shifts").group(1) == "TKDQM"


def test_identifier_patterns_compile_and_cover_cms_ids():
    patterns = defaults.load_identifier_patterns()
    by_type = {entry["id_type"]: entry for entry in patterns}
    assert "cms_repo" in by_type  # catalog-only id type, no extractor rule
    assert re.search(by_type["cms_site"]["regex"], "T2_US_MIT")
    for entry in patterns:
        assert entry["description"]


def test_linker_config_routes_reference_document_chunk():
    routes = defaults.load_linker_config()["routes"]
    for id_type, by_src in routes.items():
        assert set(by_src) == {"document_chunk"}, id_type
        route = by_src["document_chunk"]
        assert route["edge_type"] == "references"
        assert route["dst_template"] == "{resolved}"
        assert 0 < route["confidence"] <= 1


def test_cross_link_rules_file_and_unknown_name():
    path = defaults.cross_link_rules_file("global_tag_release.yaml")
    assert path.is_file()
    rules = defaults.load_cross_link_rules()
    assert [r["id"] for r in rules] == [
        "cms_global_tag_release_depends_on_cmssw_release"
    ]
    with pytest.raises(FileNotFoundError):
        defaults.cross_link_rules_file("no_such_rules.yaml")


def test_global_tag_release_linker_wraps_packaged_rule():
    linker = GlobalTagReleaseLinker()
    assert linker.name == "cms_declarative_global_tag_release"
    assert [rule.id for rule in linker.rules] == [
        "cms_global_tag_release_depends_on_cmssw_release"
    ]
    assert linker.emits_edge_types == ("depends_on",)
    assert linker.requires_narrowings == (
        ("global_tag", "depends_on", "cmssw_release"),
    )
