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

SQL_QUERY_CONVO_WITH_FEEDBACK = """
SELECT c.sender,
       c.content,
       c.message_id,
       lf.feedback
FROM conversations c
LEFT JOIN (
    SELECT DISTINCT ON (mid)
        mid,
        feedback,
        feedback_ts
    FROM feedback
    ORDER BY mid, feedback_ts DESC
) lf ON lf.mid = c.message_id
WHERE c.conversation_id = %s
ORDER BY c.message_id ASC;
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
    a2rchi_message_ts,
    insert_convo_ts,
    finish_call_ts,
    server_response_msg_ts,
    msg_duration
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
"""

SQL_CREATE_CONVERSATION = """
INSERT INTO conversation_metadata (
    title, created_at, last_message_at, client_id, a2rchi_version
)
VALUES (%s, %s, %s, %s, %s)
RETURNING conversation_id;
"""

SQL_UPDATE_CONVERSATION_TIMESTAMP = """
UPDATE conversation_metadata
SET last_message_at = %s
WHERE conversation_id = %s AND client_id = %s;
"""

SQL_LIST_CONVERSATIONS = """
SELECT conversation_id, title, created_at, last_message_at
FROM conversation_metadata
WHERE client_id = %s
ORDER BY last_message_at DESC
LIMIT %s;
"""

SQL_GET_CONVERSATION_METADATA = """
SELECT conversation_id, title, created_at, last_message_at
FROM conversation_metadata
WHERE conversation_id = %s AND client_id = %s;
"""

SQL_DELETE_CONVERSATION = """
DELETE FROM conversation_metadata
WHERE conversation_id = %s AND client_id = %s;
"""

SQL_INSERT_TOOL_CALLS = """
INSERT INTO agent_tool_calls (
    conversation_id, message_id, step_number, tool_name, tool_args, tool_result, ts
)
VALUES %s;
"""

SQL_QUERY_TOOL_CALLS = """
SELECT step_number, tool_name, tool_args, tool_result, ts
FROM agent_tool_calls
WHERE message_id = %s
ORDER BY step_number ASC;
"""
