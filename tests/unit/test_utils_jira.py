import pytest

from src.utils import jira as jira_utils


class TestParseJiraProjectKeys:
    def test_accepts_supported_jira_project_key_formats(self):
        projects = jira_utils.parse_jira_project_keys(
            ["CMSTZ", "CMSDM", "IF", "R2D2", "PRODUCT_2013", "MY_EXAMPLE_PROJECT"],
            "invalid projects",
        )

        assert projects == [
            "CMSTZ",
            "CMSDM",
            "IF",
            "R2D2",
            "PRODUCT_2013",
            "MY_EXAMPLE_PROJECT",
        ]

    def test_strips_project_key_whitespace(self):
        projects = jira_utils.parse_jira_project_keys(
            [" CMSTZ ", " CMSDM"], "invalid projects"
        )

        assert projects == ["CMSTZ", "CMSDM"]

    @pytest.mark.parametrize(
        "projects",
        [
            [],
            "CMSTZ",
            ["CMSTZ", ""],
            ["CMSTZ", 7],
            ["CMSTZ, CMSDM"],
            ["cms"],
            ["2013PROJECT"],
            ["PRODUCT-2012"],
        ],
    )
    def test_rejects_unsupported_jira_project_key_formats(self, projects):
        with pytest.raises(ValueError, match="invalid projects"):
            jira_utils.parse_jira_project_keys(projects, "invalid projects")


class TestQuoteJqlString:
    def test_quotes_and_escapes_jql_string_values(self):
        assert (
            jira_utils.quote_jql_string('Blocked "QA" \\ Needs Triage')
            == '"Blocked \\"QA\\" \\\\ Needs Triage"'
        )
