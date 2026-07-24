#!/bin/python
import time

from src.interfaces.jira import (
    JiraIssueClient,
    JiraServiceConfig,
    JiraTicketResponderService,
    build_archi_for_jira,
    resolve_jira_agent_settings,
)
from src.utils.config_access import get_services_config
from src.utils.env import read_secret
from src.utils.logging import get_logger, setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory

setup_logging()
logger = get_logger(__name__)


def resolve_jira_pat() -> str:
    pat = read_secret("JIRA_TICKET_RESPONDER_PAT")
    if pat:
        return pat
    raise ValueError("Missing Jira auth: set JIRA_TICKET_RESPONDER_PAT.")


def main() -> None:
    logger.info("Starting Jira ticket responder")

    factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))
    PostgresServiceFactory.set_instance(factory)

    services_config = get_services_config()
    jira_config = JiraServiceConfig.from_config(services_config.get("jira_ticket_responder", {}))
    pat = resolve_jira_pat()

    agent_settings = resolve_jira_agent_settings(services_config)
    archi_instance, agent_settings = build_archi_for_jira(services_config, agent_settings)

    issue_client = JiraIssueClient(jira_config.url, pat)
    service = JiraTicketResponderService(
        config=jira_config,
        issue_client=issue_client,
        archi_instance=archi_instance,
        postgres_factory=factory,
        agent_settings=agent_settings,
    )
    logger.info("Jira ticket responder lookback window: %s days", jira_config.lookback_days)

    while True:
        service.poll_once()
        time.sleep(jira_config.poll_interval_minutes * 60)


if __name__ == "__main__":
    main()
