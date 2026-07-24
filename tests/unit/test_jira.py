from contextlib import AbstractContextManager
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from src.archi.utils.output_dataclass import PipelineOutput
from src.interfaces import jira as jira_interface


class _FakeArchi:
    def __init__(self, answer="  Use the documented fix.  ", result=None):
        self.answer = answer
        self.result = result
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.result is not None:
            return self.result
        return SimpleNamespace(answer=self.answer, source_documents=[])


class _FakeIssueClient:
    def __init__(
        self,
        order=None,
        fail_post=False,
        issues=None,
        answered_issues=None,
        fail_comments=False,
    ):
        self.order = order if order is not None else []
        self.fail_post = fail_post
        self.issues = issues if issues is not None else []
        self.answered_issues = answered_issues if answered_issues is not None else set()
        self.fail_comments = fail_comments
        self.searches = []
        self.comment_fetches = []
        self.posted = []

    def search_recent_issues(self, projects, lookback_days, eligible_statuses):
        self.searches.append((projects, lookback_days, eligible_statuses))
        return list(self.issues)

    def has_comment_by_authenticated_user(self, issue_key):
        self.comment_fetches.append(issue_key)
        if self.fail_comments:
            raise RuntimeError("comments failed")
        return issue_key in self.answered_issues

    def post_restricted_comment(self, issue_key, body, visible_to_role):
        self.order.append("post")
        if self.fail_post:
            raise RuntimeError("post failed")
        self.posted.append((issue_key, body, visible_to_role))


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params):
        self.executed.append((query, params))

    def fetchone(self):
        return (42,)


class _FakeConnection:
    def __init__(self):
        self.cursor_instance = _FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeConnectionContext(AbstractContextManager):
    def __init__(self, connection):
        self.connection = connection
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        self.exited = True
        return False


class _FakeConnectionPool:
    def __init__(self):
        self.connection = _FakeConnection()
        self.connection_context = None
        self.released = []

    def get_connection(self):
        self.connection_context = _FakeConnectionContext(self.connection)
        return self.connection_context

    def release_connection(self, conn):
        self.released.append(conn)


def _raw_issue(
    *,
    key="CMSTZ-1",
    status="Open",
):
    fields = SimpleNamespace(
        summary="Broken transfer",
        description="Transfers fail with timeout.",
        status=SimpleNamespace(name=status),
    )
    return SimpleNamespace(key=key, fields=fields)


def _service(issue_client, archi_instance, projects=None, eligible_statuses=None):
    config = jira_interface.JiraServiceConfig(
        url="https://jira.example/",
        projects=projects if projects is not None else ["CMSTZ"],
        visible_to_role="Developers",
        poll_interval_minutes=1,
        lookback_days=7,
        eligible_statuses=(
            eligible_statuses
            if eligible_statuses is not None
            else ["Open", "In Progress"]
        ),
    )
    return jira_interface.JiraTicketResponderService(
        config=config,
        issue_client=issue_client,
        archi_instance=archi_instance,
        postgres_factory=SimpleNamespace(connection_pool=None),
        agent_settings=SimpleNamespace(
            agent_class="CMSCompOpsAgent",
            model_provider="openai/gpt-5",
        ),
    )


class TestJiraServiceConfig:
    def test_from_config_defaults_poll_interval_minutes_to_one(self):
        config = jira_interface.JiraServiceConfig.from_config(
            {
                "url": "https://jira.example/",
                "projects": ["CMSTZ", "CMSDM"],
                "visible_to_role": "Developers",
            }
        )

        assert config.poll_interval_minutes == 1
        assert config.lookback_days == 7
        assert config.projects == ["CMSTZ", "CMSDM"]
        assert config.eligible_statuses == ["Open", "In Progress"]

    def test_from_config_reads_poll_interval_minutes_lookback_days_and_eligible_statuses(
        self,
    ):
        config = jira_interface.JiraServiceConfig.from_config(
            {
                "url": "https://jira.example/",
                "projects": ["CMSTZ"],
                "visible_to_role": "Developers",
                "poll_interval_minutes": 5,
                "lookback_days": 14,
                "eligible_statuses": ["Open", "Triaged"],
            }
        )

        assert config.poll_interval_minutes == 5
        assert config.lookback_days == 14
        assert config.eligible_statuses == ["Open", "Triaged"]

    def test_from_config_defaults_empty_optional_fields(self):
        config = jira_interface.JiraServiceConfig.from_config(
            {
                "url": "https://jira.example/",
                "projects": ["CMSTZ"],
                "visible_to_role": "Developers",
                "lookback_days": "",
                "eligible_statuses": "",
            }
        )

        assert config.lookback_days == 7
        assert config.eligible_statuses == ["Open", "In Progress"]

    @pytest.mark.parametrize("value", [0, -1, "x", True])
    def test_from_config_rejects_invalid_lookback_days(self, value):
        with pytest.raises(
            ValueError, match="services.jira_ticket_responder.lookback_days"
        ):
            jira_interface.JiraServiceConfig.from_config(
                {
                    "url": "https://jira.example/",
                    "projects": ["CMSTZ"],
                    "visible_to_role": "Developers",
                    "poll_interval_minutes": 1,
                    "lookback_days": value,
                }
            )

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
    def test_from_config_rejects_invalid_projects(self, projects):
        with pytest.raises(ValueError, match="services.jira_ticket_responder.projects"):
            jira_interface.JiraServiceConfig.from_config(
                {
                    "url": "https://jira.example/",
                    "projects": projects,
                    "visible_to_role": "Developers",
                    "poll_interval_minutes": 1,
                }
            )


class TestJiraAgentSettings:
    def test_resolve_agent_settings_prefers_jira_provider_and_model(self):
        settings = jira_interface.resolve_jira_agent_settings(
            {
                "jira_ticket_responder": {
                    "default_provider": "openai",
                    "default_model": "gpt-5",
                },
                "chat_app": {
                    "default_provider": "anthropic",
                    "default_model": "claude-sonnet-4-20250514",
                    "agents_dir": "/chat/agents",
                },
            }
        )

        assert settings.default_provider == "openai"
        assert settings.default_model == "gpt-5"
        assert str(settings.agents_dir) == "/chat/agents"

    def test_resolve_agent_settings_falls_back_to_chat_provider_and_model(self):
        settings = jira_interface.resolve_jira_agent_settings(
            {
                "jira_ticket_responder": {},
                "chat_app": {
                    "default_provider": "openai",
                    "default_model": "gpt-5",
                },
            }
        )

        assert settings.agent_class == "CMSCompOpsAgent"
        assert settings.default_provider == "openai"
        assert settings.default_model == "gpt-5"

    def test_resolve_agent_settings_requires_resolved_provider_and_model(self):
        with pytest.raises(
            ValueError, match="services.jira_ticket_responder or services.chat_app"
        ):
            jira_interface.resolve_jira_agent_settings(
                {"jira_ticket_responder": {}, "chat_app": {}}
            )


class TestJiraTicketPrompt:
    def test_build_ticket_prompt_excludes_comments(self):
        issue = jira_interface.JiraIssue(
            key="CMSTZ-7",
            summary="Storage is unavailable",
            description="The site reports storage errors.",
            status_name="Open",
        )

        prompt = jira_interface.build_ticket_prompt(issue)

        assert prompt == (
            "Suggest a solution to this problem.\n\n"
            "Issue:\n"
            "CMSTZ-7\n\n"
            "Summary:\n"
            "Storage is unavailable\n\n"
            "Status:\n"
            "Open\n\n"
            "Description:\n"
            "The site reports storage errors."
        )


class TestJiraIssueEligibility:
    def test_open_issue_is_eligible(self):
        issue = jira_interface.JiraIssue(
            key="CMSTZ-7",
            summary="Open",
            description="Open",
            status_name="Open",
        )

        assert jira_interface.is_issue_eligible(issue, ["Open", "Triaged"]) is True

    def test_configured_issue_status_is_eligible(self):
        issue = jira_interface.JiraIssue(
            key="CMSTZ-7",
            summary="Triaged",
            description="Triaged",
            status_name="Triaged",
        )

        assert jira_interface.is_issue_eligible(issue, ["Open", "Triaged"]) is True

    @pytest.mark.parametrize("status", ["Closed", "Resolved", "To Do"])
    def test_disallowed_status_is_not_eligible(self, status):
        issue = jira_interface.JiraIssue(
            key="CMSTZ-1",
            summary="Wrong status",
            description="Wrong status",
            status_name=status,
        )

        assert jira_interface.is_issue_eligible(issue, ["Open", "Triaged"]) is False


class TestJiraCommentTraceFormatting:
    def test_builds_standard_wiki_panels_for_reasoning_and_tool_calls(self):
        result = PipelineOutput(
            answer="Use the documented fix.",
            messages=[
                SimpleNamespace(
                    additional_kwargs={
                        "reasoning_content": "Checked the transfer logs."
                    },
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "retriever",
                            "args": {"query": "transfer timeout"},
                        }
                    ],
                ),
                SimpleNamespace(
                    tool_call_id="call-1",
                    content="Found the transfer timeout runbook.",
                ),
            ],
        )

        comment_body = jira_interface.build_jira_comment_body(
            "Use the documented fix.", result
        )

        assert comment_body == (
            "Use the documented fix.\n\n"
            "{panel:title=Reasoning trace}\n"
            "{noformat}\n"
            "Checked the transfer logs.\n"
            "{noformat}\n"
            "{panel}\n\n"
            "{panel:title=Tool calls}\n"
            "{noformat}\n"
            "Tool call 1: retriever\n"
            "Input:\n"
            "{\n"
            '  "query": "transfer timeout"\n'
            "}\n"
            "\n"
            "Output:\n"
            "Found the transfer timeout runbook.\n"
            "{noformat}\n"
            "{panel}"
        )
        assert "{expand" not in comment_body

    def test_escapes_noformat_macro_inside_trace_text(self):
        result = PipelineOutput(
            answer="Use the documented fix.",
            messages=[
                SimpleNamespace(
                    additional_kwargs={
                        "reasoning_content": "A tool returned {noformat} in text."
                    },
                    tool_calls=[],
                ),
            ],
        )

        comment_body = jira_interface.build_jira_comment_body(
            "Use the documented fix.", result
        )

        assert comment_body == (
            "Use the documented fix.\n\n"
            "{panel:title=Reasoning trace}\n"
            "{noformat}\n"
            "A tool returned { noformat } in text.\n"
            "{noformat}\n"
            "{panel}"
        )

    def test_caps_tool_calls_panel_as_one_closed_wiki_section(self):
        result = PipelineOutput(
            answer="Use the documented fix.",
            messages=[
                SimpleNamespace(
                    tool_calls=[
                        {
                            "id": f"call-{index}",
                            "name": "retriever",
                            "args": {"query": f"query-{index}", "payload": "x" * 1000},
                        }
                        for index in range(10)
                    ],
                ),
                *[
                    SimpleNamespace(
                        tool_call_id=f"call-{index}",
                        content=f"result-{index} " + ("y" * 1000),
                    )
                    for index in range(10)
                ],
            ],
        )

        comment_body = jira_interface.build_jira_comment_body(
            "Use the documented fix.", result
        )

        assert "[truncated " in comment_body
        assert comment_body.endswith("{panel}")
        assert comment_body.count("{panel:title=Tool calls}") == 1
        assert comment_body.count("{noformat}") == 2
        assert len(comment_body) < jira_interface.JIRA_TRACE_SECTION_MAX_CHARS + 200


class TestJiraTicketResponderService:
    def test_poll_once_searches_multiple_projects_and_answers_each_eligible_issue(self):
        issue_client = _FakeIssueClient(
            issues=[
                _raw_issue(key="CMSTZ-1"),
                _raw_issue(key="CMSDM-2"),
            ]
        )
        archi_instance = _FakeArchi()
        service = _service(issue_client, archi_instance, projects=["CMSTZ", "CMSDM"])
        service.persist_interaction = Mock()

        service.poll_once()

        assert issue_client.searches == [
            (["CMSTZ", "CMSDM"], 7, ["Open", "In Progress"])
        ]
        assert issue_client.comment_fetches == ["CMSTZ-1", "CMSDM-2"]
        assert len(archi_instance.calls) == 2
        assert archi_instance.calls[0]["history"][0][1] == (
            "Suggest a solution to this problem.\n\n"
            "Issue:\n"
            "CMSTZ-1\n\n"
            "Summary:\n"
            "Broken transfer\n\n"
            "Status:\n"
            "Open\n\n"
            "Description:\n"
            "Transfers fail with timeout."
        )
        assert archi_instance.calls[1]["history"][0][1] == (
            "Suggest a solution to this problem.\n\n"
            "Issue:\n"
            "CMSDM-2\n\n"
            "Summary:\n"
            "Broken transfer\n\n"
            "Status:\n"
            "Open\n\n"
            "Description:\n"
            "Transfers fail with timeout."
        )
        assert issue_client.posted == [
            ("CMSTZ-1", "Use the documented fix.", "Developers"),
            ("CMSDM-2", "Use the documented fix.", "Developers"),
        ]
        assert service.persist_interaction.call_count == 2
        service.persist_interaction.assert_any_call(
            "CMSTZ-1",
            archi_instance.calls[0]["history"][0][1],
            "Use the documented fix.",
            [],
        )
        service.persist_interaction.assert_any_call(
            "CMSDM-2",
            archi_instance.calls[1]["history"][0][1],
            "Use the documented fix.",
            [],
        )

    @patch("src.interfaces.jira.psycopg2.extras.execute_values")
    def test_poll_once_e2e_answers_multiple_projects_and_persists(self, execute_values):
        issue_client = _FakeIssueClient(
            issues=[
                _raw_issue(key="CMSTZ-1"),
                _raw_issue(key="CMSDM-2"),
            ]
        )
        pool = _FakeConnectionPool()
        service = _service(issue_client, _FakeArchi(), projects=["CMSTZ", "CMSDM"])
        service.postgres_factory = SimpleNamespace(connection_pool=pool)

        service.poll_once()

        assert issue_client.searches == [
            (["CMSTZ", "CMSDM"], 7, ["Open", "In Progress"])
        ]
        assert issue_client.comment_fetches == ["CMSTZ-1", "CMSDM-2"]
        assert issue_client.posted == [
            ("CMSTZ-1", "Use the documented fix.", "Developers"),
            ("CMSDM-2", "Use the documented fix.", "Developers"),
        ]
        assert pool.connection.commits == 2
        assert pool.connection.rollbacks == 0
        assert pool.connection.cursor_instance.executed[0][1][0] == "Jira issue CMSTZ-1"
        assert pool.connection.cursor_instance.executed[1][1][0] == "Jira issue CMSDM-2"
        assert execute_values.call_count == 2

    def test_process_issue_posts_before_persisting(self):
        order = []
        issue_client = _FakeIssueClient(order=order)
        archi_instance = _FakeArchi()
        service = _service(issue_client, archi_instance)
        service.persist_interaction = Mock(
            side_effect=lambda *args: order.append("persist")
        )

        processed = service.process_issue(_raw_issue())

        assert processed is True
        assert issue_client.comment_fetches == ["CMSTZ-1"]
        assert archi_instance.calls == [
            {
                "history": [
                    (
                        "User",
                        "Suggest a solution to this problem.\n\n"
                        "Issue:\n"
                        "CMSTZ-1\n\n"
                        "Summary:\n"
                        "Broken transfer\n\n"
                        "Status:\n"
                        "Open\n\n"
                        "Description:\n"
                        "Transfers fail with timeout.",
                    )
                ]
            }
        ]
        assert issue_client.posted == [
            ("CMSTZ-1", "Use the documented fix.", "Developers")
        ]
        assert order == ["post", "persist"]
        service.persist_interaction.assert_called_once_with(
            "CMSTZ-1",
            archi_instance.calls[0]["history"][0][1],
            "Use the documented fix.",
            [],
        )

    def test_process_issue_posts_answer_with_trace_but_persists_plain_answer(self):
        result = PipelineOutput(
            answer="  Use the documented fix.  ",
            messages=[
                SimpleNamespace(
                    additional_kwargs={"reasoning_content": "Matched symptoms."},
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "local_files",
                            "args": {"path": "runbook.md"},
                        }
                    ],
                ),
                SimpleNamespace(tool_call_id="call-1", content="Runbook section 2"),
            ],
            source_documents=[],
        )
        issue_client = _FakeIssueClient()
        archi_instance = _FakeArchi(result=result)
        service = _service(issue_client, archi_instance)
        service.persist_interaction = Mock()

        processed = service.process_issue(_raw_issue())

        assert processed is True
        assert issue_client.posted == [
            (
                "CMSTZ-1",
                "Use the documented fix.\n\n"
                "{panel:title=Reasoning trace}\n"
                "{noformat}\n"
                "Matched symptoms.\n"
                "{noformat}\n"
                "{panel}\n\n"
                "{panel:title=Tool calls}\n"
                "{noformat}\n"
                "Tool call 1: local_files\n"
                "Input:\n"
                "{\n"
                '  "path": "runbook.md"\n'
                "}\n"
                "\n"
                "Output:\n"
                "Runbook section 2\n"
                "{noformat}\n"
                "{panel}",
                "Developers",
            )
        ]
        service.persist_interaction.assert_called_once_with(
            "CMSTZ-1",
            archi_instance.calls[0]["history"][0][1],
            "Use the documented fix.",
            [],
        )

    def test_post_failure_skips_persistence(self):
        order = []
        issue_client = _FakeIssueClient(order=order, fail_post=True)
        archi_instance = _FakeArchi()
        service = _service(issue_client, archi_instance)
        service.persist_interaction = Mock()

        processed = service.process_issue(_raw_issue())

        assert processed is False
        assert archi_instance.calls == [
            {
                "history": [
                    (
                        "User",
                        "Suggest a solution to this problem.\n\n"
                        "Issue:\n"
                        "CMSTZ-1\n\n"
                        "Summary:\n"
                        "Broken transfer\n\n"
                        "Status:\n"
                        "Open\n\n"
                        "Description:\n"
                        "Transfers fail with timeout.",
                    )
                ]
            }
        ]
        assert order == ["post"]
        service.persist_interaction.assert_not_called()

    def test_persistence_failure_keeps_posted_comment(self):
        issue_client = _FakeIssueClient()
        service = _service(issue_client, _FakeArchi())
        service.persist_interaction = Mock(side_effect=RuntimeError("db failed"))

        processed = service.process_issue(_raw_issue())

        assert processed is True
        assert issue_client.posted == [
            ("CMSTZ-1", "Use the documented fix.", "Developers")
        ]
        service.persist_interaction.assert_called_once_with(
            "CMSTZ-1",
            "Suggest a solution to this problem.\n\n"
            "Issue:\n"
            "CMSTZ-1\n\n"
            "Summary:\n"
            "Broken transfer\n\n"
            "Status:\n"
            "Open\n\n"
            "Description:\n"
            "Transfers fail with timeout.",
            "Use the documented fix.",
            [],
        )

    def test_archi_empty_answer_skips_posting(self):
        issue_client = _FakeIssueClient()
        service = _service(issue_client, _FakeArchi(answer="   "))
        service.persist_interaction = Mock()

        processed = service.process_issue(_raw_issue())

        assert processed is False
        assert issue_client.posted == []
        service.persist_interaction.assert_not_called()

    def test_existing_service_account_comment_is_skipped_without_archi_call(self):
        issue_client = _FakeIssueClient(
            answered_issues={"CMSTZ-1"},
        )
        archi_instance = _FakeArchi()
        service = _service(issue_client, archi_instance)
        service.persist_interaction = Mock()

        processed = service.process_issue(_raw_issue())

        assert processed is False
        assert issue_client.comment_fetches == ["CMSTZ-1"]
        assert archi_instance.calls == []
        assert issue_client.posted == []

    def test_comment_fetch_failure_skips_answering(self):
        issue_client = _FakeIssueClient(fail_comments=True)
        archi_instance = _FakeArchi()
        service = _service(issue_client, archi_instance)
        service.persist_interaction = Mock()

        processed = service.process_issue(_raw_issue())

        assert processed is False
        assert issue_client.comment_fetches == ["CMSTZ-1"]
        assert archi_instance.calls == []
        assert issue_client.posted == []
        service.persist_interaction.assert_not_called()


class TestJiraTicketResponderPersistence:
    @patch("src.interfaces.jira.psycopg2.extras.execute_values")
    def test_persist_interaction_uses_context_managed_pool_connection(
        self, execute_values
    ):
        service = _service(_FakeIssueClient(), _FakeArchi())
        pool = _FakeConnectionPool()
        service.postgres_factory = SimpleNamespace(connection_pool=pool)

        service.persist_interaction(
            "CMSTZ-1",
            "Suggest a solution.",
            "Use the documented fix.",
            [],
        )

        assert pool.connection_context.entered is True
        assert pool.connection_context.exited is True
        assert pool.released == []
        assert pool.connection.commits == 1
        assert pool.connection.rollbacks == 0
        assert pool.connection.cursor_instance.executed[0][1][0] == "Jira issue CMSTZ-1"
        execute_values.assert_called_once()

    @patch("src.interfaces.jira.psycopg2.extras.execute_values")
    def test_persist_interaction_rolls_back_and_exits_context_on_insert_failure(
        self, execute_values
    ):
        service = _service(_FakeIssueClient(), _FakeArchi())
        pool = _FakeConnectionPool()
        service.postgres_factory = SimpleNamespace(connection_pool=pool)
        execute_values.side_effect = RuntimeError("insert failed")

        with pytest.raises(RuntimeError, match="insert failed"):
            service.persist_interaction(
                "CMSTZ-1",
                "Suggest a solution.",
                "Use the documented fix.",
                [],
            )

        assert pool.connection_context.entered is True
        assert pool.connection_context.exited is True
        assert pool.released == []
        assert pool.connection.commits == 0
        assert pool.connection.rollbacks == 1


class TestJiraIssueClient:
    @patch("src.interfaces.jira.jira.JIRA")
    def test_constructor_validates_login_before_polling(self, jira_cls):
        jira_cls.return_value.myself.side_effect = RuntimeError("bad auth")

        with pytest.raises(RuntimeError, match="Failed to log in to Jira"):
            jira_interface.JiraIssueClient("https://jira.example/", "pat")

        jira_cls.assert_called_once_with(
            "https://jira.example/", token_auth="pat", timeout=30
        )
        jira_cls.return_value.myself.assert_called_once_with()

    @patch("src.interfaces.jira.jira.JIRA")
    def test_constructor_stores_authenticated_account_id(self, jira_cls):
        jira_cls.return_value.myself.return_value = {"accountId": "service-account-id"}

        client = jira_interface.JiraIssueClient("https://jira.example/", "pat")

        assert client.user_identities == {"accountId": "service-account-id"}
        jira_cls.return_value.myself.assert_called_once_with()

    @patch("src.interfaces.jira.jira.JIRA")
    def test_constructor_stores_data_center_user_key(self, jira_cls):
        jira_cls.return_value.myself.return_value = {
            "key": "service-user-key",
            "name": "service-user-name",
        }

        client = jira_interface.JiraIssueClient("https://jira.example/", "pat")

        assert client.user_identities == {
            "key": "service-user-key",
            "name": "service-user-name",
        }
        jira_cls.return_value.myself.assert_called_once_with()

    @patch("src.interfaces.jira.jira.JIRA")
    def test_constructor_falls_back_to_data_center_user_name(self, jira_cls):
        jira_cls.return_value.myself.return_value = {"name": "service-user-name"}

        client = jira_interface.JiraIssueClient("https://jira.example/", "pat")

        assert client.user_identities == {"name": "service-user-name"}
        jira_cls.return_value.myself.assert_called_once_with()

    @patch("src.interfaces.jira.jira.JIRA")
    def test_constructor_rejects_missing_user_identity(self, jira_cls):
        jira_cls.return_value.myself.return_value = {"displayName": "Service User"}

        with pytest.raises(
            RuntimeError, match="Failed to resolve Jira service account identity"
        ):
            jira_interface.JiraIssueClient("https://jira.example/", "pat")

        jira_cls.return_value.myself.assert_called_once_with()

    def test_search_recent_issues_uses_rolling_lookback_and_does_not_request_comments(
        self,
    ):
        client = object.__new__(jira_interface.JiraIssueClient)
        client.client = SimpleNamespace(search_issues=Mock(return_value=[]))

        list(client.search_recent_issues(["CMSTZ", "IF"], 7, ["Open", 'Blocked "QA"']))

        client.client.search_issues.assert_called_once_with(
            'project in ("CMSTZ", "IF") AND status in ("Open", "Blocked \\"QA\\"") AND updated >= "-7d" ORDER BY updated ASC',
            startAt=0,
            maxResults=100,
            fields=["summary", "description", "status"],
        )

    def test_has_comment_by_authenticated_user_reads_newest_comments_first(self):
        client = object.__new__(jira_interface.JiraIssueClient)
        client.user_identities = {"accountId": "service-account-id"}
        client.client = SimpleNamespace(
            _get_json=Mock(
                return_value={
                    "comments": [
                        {"author": {"accountId": "human-account-id"}},
                        {"author": {"accountId": "service-account-id"}},
                        {
                            "body": "This would fail if comments after the match were inspected."
                        },
                    ],
                    "total": 3,
                }
            )
        )

        has_service_comment = client.has_comment_by_authenticated_user("CMSTZ-1")

        assert has_service_comment is True
        client.client._get_json.assert_called_once_with(
            "issue/CMSTZ-1/comment",
            params={"startAt": 0, "maxResults": 100, "orderBy": "-created"},
        )

    def test_has_comment_by_authenticated_user_stops_after_matching_page(self):
        client = object.__new__(jira_interface.JiraIssueClient)
        client.user_identities = {"accountId": "service-account-id"}
        client.client = SimpleNamespace(
            _get_json=Mock(
                side_effect=[
                    {
                        "comments": [
                            {"author": {"accountId": f"human-account-{index}"}}
                            for index in range(100)
                        ],
                        "total": 250,
                    },
                    {
                        "comments": [
                            {"author": {"accountId": "service-account-id"}},
                        ],
                        "total": 250,
                    },
                ]
            )
        )

        has_service_comment = client.has_comment_by_authenticated_user("CMSTZ-1")

        assert has_service_comment is True
        assert client.client._get_json.call_args_list == [
            call(
                "issue/CMSTZ-1/comment",
                params={"startAt": 0, "maxResults": 100, "orderBy": "-created"},
            ),
            call(
                "issue/CMSTZ-1/comment",
                params={"startAt": 100, "maxResults": 100, "orderBy": "-created"},
            ),
        ]

    def test_has_comment_by_authenticated_user_returns_false_after_all_pages(self):
        client = object.__new__(jira_interface.JiraIssueClient)
        client.user_identities = {"accountId": "service-account-id"}
        client.client = SimpleNamespace(
            _get_json=Mock(
                side_effect=[
                    {
                        "comments": [
                            {"author": {"accountId": f"human-account-{index}"}}
                            for index in range(100)
                        ],
                        "total": 101,
                    },
                    {
                        "comments": [{"author": {"accountId": "human-account-100"}}],
                        "total": 101,
                    },
                ]
            )
        )

        has_service_comment = client.has_comment_by_authenticated_user("CMSTZ-1")

        assert has_service_comment is False
        assert client.client._get_json.call_args_list == [
            call(
                "issue/CMSTZ-1/comment",
                params={"startAt": 0, "maxResults": 100, "orderBy": "-created"},
            ),
            call(
                "issue/CMSTZ-1/comment",
                params={"startAt": 100, "maxResults": 100, "orderBy": "-created"},
            ),
        ]

    def test_has_comment_by_authenticated_user_matches_data_center_author_key(self):
        client = object.__new__(jira_interface.JiraIssueClient)
        client.user_identities = {
            "key": "service-user-key",
            "name": "service-user-name",
        }
        client.client = SimpleNamespace(
            _get_json=Mock(
                return_value={
                    "comments": [
                        {
                            "author": {
                                "key": "service-user-key",
                                "name": "service-user-name",
                            }
                        },
                    ],
                    "total": 1,
                }
            )
        )

        has_service_comment = client.has_comment_by_authenticated_user("CMSTZ-1")

        assert has_service_comment is True
        client.client._get_json.assert_called_once_with(
            "issue/CMSTZ-1/comment",
            params={"startAt": 0, "maxResults": 100, "orderBy": "-created"},
        )

    def test_has_comment_by_authenticated_user_matches_data_center_author_name(self):
        client = object.__new__(jira_interface.JiraIssueClient)
        client.user_identities = {
            "key": "service-user-key",
            "name": "service-user-name",
        }
        client.client = SimpleNamespace(
            _get_json=Mock(
                return_value={
                    "comments": [
                        {"author": {"name": "service-user-name"}},
                    ],
                    "total": 1,
                }
            )
        )

        has_service_comment = client.has_comment_by_authenticated_user("CMSTZ-1")

        assert has_service_comment is True
        client.client._get_json.assert_called_once_with(
            "issue/CMSTZ-1/comment",
            params={"startAt": 0, "maxResults": 100, "orderBy": "-created"},
        )

    def test_post_restricted_comment_uses_role_visibility(self):
        client = object.__new__(jira_interface.JiraIssueClient)
        client.client = SimpleNamespace(add_comment=Mock())

        client.post_restricted_comment("CMSTZ-1", "Fix it", "Developers")

        client.client.add_comment.assert_called_once_with(
            "CMSTZ-1",
            "Fix it",
            visibility={"type": "role", "value": "Developers"},
        )
