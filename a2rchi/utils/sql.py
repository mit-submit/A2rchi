"""SQL queries used by A2rchi"""
SQL_INSERT_CONVO = """
INSERT INTO conversations (
    a2rchi_service, conversation_id, sender, content, link, context, ts, conf_id
)
VALUES %s
RETURNING message_id;
"""

SQL_INSERT_CONFIG = """
INSERT INTO configs (
    config, config_name
)
VALUES %s
RETURNING config_id;
"""

SQL_INSERT_FEEDBACK = """
INSERT INTO feedback (
    mid, feedback_ts, feedback, feedback_msg, incorrect, unhelpful, inappropriate
)
VALUES (%s, %s, %s, %s, %s, %s, %s);
"""

SQL_QUERY_CONVO = """
SELECT sender, content
FROM conversations
WHERE conversation_id = %s
ORDER BY message_id ASC;
"""

SQL_INSERT_TIMING = """
INSERT INTO timing (
    mid,
    client_sent_msg_ts,
    server_received_msg_ts,
    lock_acquisition_ts,
    vectorstore_update_ts,
    query_convo_history_ts,
    chain_finished_ts,
    similarity_search_ts,
    a2rchi_message_ts,
    insert_convo_ts,
    finish_call_ts,
    server_response_msg_ts,
    msg_duration
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
"""
