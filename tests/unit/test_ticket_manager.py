"""Unit tests for TicketManager._collect_from_client error handling.

Regression for bug #11: generator iteration outside try/except
caused AuthError from Redmine to crash the entire ingestion pipeline.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestCollectFromClientErrorHandling:
    """Verify _collect_from_client handles errors during generator iteration."""

    def _make_manager(self):
        """Create a TicketManager with mocked config dependencies."""
        with patch("src.data_manager.collectors.tickets.ticket_manager.get_global_config") as mock_gc:
            mock_gc.return_value = {"DATA_PATH": "/tmp/test_data"}
            with patch("src.data_manager.collectors.tickets.ticket_manager.JiraClient"):
                with patch("src.data_manager.collectors.tickets.ticket_manager.RedmineClient"):
                    from src.data_manager.collectors.tickets.ticket_manager import TicketManager
                    dm_config = {
                        "sources": {
                            "jira": {"enabled": False},
                            "redmine": {"enabled": False},
                        }
                    }
                    return TicketManager(dm_config=dm_config)

    def test_generator_exception_caught_gracefully(self):
        """If client.collect() returns a generator that raises mid-iteration,
        the error must be caught and logged, not propagated."""
        manager = self._make_manager()
        persistence = MagicMock()

        # Create a client whose collect() returns a generator that raises
        mock_client = MagicMock()
        call_count = 0

        def failing_generator(**kwargs):
            nonlocal call_count
            yield MagicMock()  # First resource succeeds
            call_count += 1
            raise Exception("AuthError: Invalid API key")

        mock_client.collect.return_value = failing_generator()

        # Should NOT raise — the exception should be caught
        manager._collect_from_client(
            mock_client, "Redmine",
            persistence=persistence,
            overwrite=False,
            projects=["test-project"],
        )

        # First resource should have been persisted
        assert persistence.persist_resource.call_count == 1

    def test_immediate_exception_caught(self):
        """If client.collect() raises immediately (not a generator),
        the error should still be caught."""
        manager = self._make_manager()
        persistence = MagicMock()

        mock_client = MagicMock()
        mock_client.collect.side_effect = ConnectionError("connection refused")

        # Should NOT raise
        manager._collect_from_client(
            mock_client, "JIRA",
            persistence=persistence,
            overwrite=False,
            projects=["test-project"],
        )
        assert persistence.persist_resource.call_count == 0

    def test_successful_collection(self):
        """Verify normal collection works: all resources persisted."""
        manager = self._make_manager()
        persistence = MagicMock()

        mock_client = MagicMock()
        resources = [MagicMock(), MagicMock(), MagicMock()]
        mock_client.collect.return_value = iter(resources)

        manager._collect_from_client(
            mock_client, "JIRA",
            persistence=persistence,
            overwrite=False,
            projects=["proj-a", "proj-b"],
        )
        assert persistence.persist_resource.call_count == 3

    def test_none_client_skipped(self):
        """If client is None, nothing should happen."""
        manager = self._make_manager()
        persistence = MagicMock()

        manager._collect_from_client(
            None, "Redmine",
            persistence=persistence,
            overwrite=False,
            projects=["test"],
        )
        assert persistence.persist_resource.call_count == 0

    def test_jira_projects_tracked(self):
        """After JIRA collection, projects should be added to jira_projects set."""
        manager = self._make_manager()
        persistence = MagicMock()

        mock_client = MagicMock()
        mock_client.collect.return_value = iter([])

        manager._collect_from_client(
            mock_client, "JIRA",
            persistence=persistence,
            overwrite=False,
            projects=["my-project"],
        )
        assert "my-project" in manager.jira_projects

    def test_redmine_projects_tracked(self):
        """After Redmine collection, projects should be added to redmine_projects set."""
        manager = self._make_manager()
        persistence = MagicMock()

        mock_client = MagicMock()
        mock_client.collect.return_value = iter([])

        manager._collect_from_client(
            mock_client, "Redmine",
            persistence=persistence,
            overwrite=False,
            projects=["infra-project"],
        )
        assert "infra-project" in manager.redmine_projects
