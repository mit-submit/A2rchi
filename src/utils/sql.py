"""SQL queries used by archi"""

# =============================================================================
# Conversation Queries
# =============================================================================

SQL_INSERT_CONVO = """
INSERT INTO conversations (
    archi_service, conversation_id, sender, content, link, context, ts,
    model_used, pipeline_used
)
VALUES %s
RETURNING message_id;
"""

SQL_INSERT_FEEDBACK = """
INSERT INTO feedback (
    mid, feedback_ts, feedback, feedback_msg, incorrect, unhelpful, inappropriate
)
VALUES (%s, %s, %s, %s, %s, %s, %s);
"""

SQL_DELETE_REACTION_FEEDBACK = """
DELETE FROM feedback
WHERE mid = %s AND feedback IN ('like', 'dislike');
"""

SQL_GET_REACTION_FEEDBACK = """
SELECT feedback FROM feedback
WHERE mid = %s AND feedback IN ('like', 'dislike')
ORDER BY feedback_ts DESC
LIMIT 1;
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
       lf.feedback,
       COALESCE(cf.comment_count, 0) AS comment_count,
       c.model_used
FROM conversations c
LEFT JOIN (
    SELECT DISTINCT ON (mid)
        mid,
        feedback,
        feedback_ts
    FROM feedback
    WHERE feedback IN ('like', 'dislike')
    ORDER BY mid, feedback_ts DESC
) lf ON lf.mid = c.message_id
LEFT JOIN (
    SELECT mid,
           COUNT(*) AS comment_count
    FROM feedback
    WHERE feedback = 'comment'
    GROUP BY mid
) cf ON cf.mid = c.message_id
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
    archi_message_ts,
    insert_convo_ts,
    finish_call_ts,
    server_response_msg_ts,
    msg_duration
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
"""

# =============================================================================
# Conversation Metadata Queries
# =============================================================================

SQL_CREATE_CONVERSATION = """
INSERT INTO conversation_metadata (
    title, created_at, last_message_at, client_id, archi_version, user_id
)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING conversation_id;
"""

SQL_UPSERT_CONVERSATION_METADATA = """
INSERT INTO conversation_metadata (
    conversation_id, title, created_at, last_message_at, client_id, archi_version
)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (conversation_id) DO UPDATE
SET last_message_at = EXCLUDED.last_message_at;
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

# User-ID-based variants (used when the user is authenticated)
# Each query also falls back to client_id so that conversations created before
# user_id was populated remain accessible.
SQL_LIST_CONVERSATIONS_BY_USER = """
SELECT conversation_id, title, created_at, last_message_at
FROM conversation_metadata
WHERE user_id = %s OR client_id = %s
ORDER BY last_message_at DESC
LIMIT %s;
"""

SQL_GET_CONVERSATION_METADATA_BY_USER = """
SELECT conversation_id, title, created_at, last_message_at
FROM conversation_metadata
WHERE conversation_id = %s AND (user_id = %s OR client_id = %s);
"""

SQL_DELETE_CONVERSATION_BY_USER = """
DELETE FROM conversation_metadata
WHERE conversation_id = %s AND (user_id = %s OR client_id = %s);
"""

SQL_UPDATE_CONVERSATION_TIMESTAMP_BY_USER = """
UPDATE conversation_metadata
SET last_message_at = %s
WHERE conversation_id = %s AND (user_id = %s OR client_id = %s);
"""

# =============================================================================
# Cross-platform (Mattermost ↔ web-chat) conversation queries
# =============================================================================

# Look up integer conversation_id by external source_ref (e.g. "mm_thread_xxx")
SQL_MM_GET_CONV_ID_BY_SOURCE_REF = """
SELECT conversation_id FROM conversation_metadata
WHERE source_ref = %s
LIMIT 1;
"""

# Create a conversation_metadata row for a Mattermost thread/channel
SQL_MM_CREATE_CONVERSATION = """
INSERT INTO conversation_metadata (
    title, created_at, last_message_at, client_id, archi_version, archi_service, source_ref
)
VALUES (%s, %s, %s, %s, %s, 'mattermost', %s)
RETURNING conversation_id;
"""

# Update last_message_at for a Mattermost conversation (matched by integer id + source_ref)
SQL_MM_UPDATE_CONVERSATION_TIMESTAMP = """
UPDATE conversation_metadata
SET last_message_at = %s
WHERE conversation_id = %s;
"""

# List conversations for an authenticated user, including Mattermost ones linked by
# mm_client_id = "mm_user_{preferred_username}".  Three ownership checks:
#   1. user_id matches (web-chat SSO conversations)
#   2. client_id matches (anonymous / browser-keyed conversations)
#   3. client_id = mm_client_id  (Mattermost-originated conversations)
SQL_LIST_CONVERSATIONS_ALL_SOURCES = """
SELECT conversation_id, title, created_at, last_message_at,
       COALESCE(archi_service, 'chat') AS archi_service
FROM conversation_metadata
WHERE user_id = %s OR client_id = %s OR client_id = %s
ORDER BY last_message_at DESC
LIMIT %s;
"""

# Fetch metadata for a single conversation, accepting any of the three ownership proofs
SQL_GET_CONVERSATION_METADATA_ALL_SOURCES = """
SELECT conversation_id, title, created_at, last_message_at,
       COALESCE(archi_service, 'chat') AS archi_service
FROM conversation_metadata
WHERE conversation_id = %s AND (user_id = %s OR client_id = %s OR client_id = %s);
"""

# =============================================================================
# Tool Calls Queries
# =============================================================================

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

# =============================================================================
# A/B Comparison Queries
# =============================================================================

SQL_INSERT_AB_COMPARISON = """
INSERT INTO ab_comparisons (
    conversation_id, user_prompt_mid, response_a_mid, response_b_mid, 
    model_a, pipeline_a, model_b, pipeline_b, is_config_a_first
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING comparison_id;
"""

SQL_UPDATE_AB_PREFERENCE = """
UPDATE ab_comparisons
SET preference = %s, preference_ts = %s
WHERE comparison_id = %s;
"""

SQL_GET_AB_COMPARISON = """
SELECT comparison_id, conversation_id, user_prompt_mid, response_a_mid, response_b_mid,
       model_a, pipeline_a, model_b, pipeline_b, 
       is_config_a_first, preference, preference_ts, created_at
FROM ab_comparisons
WHERE comparison_id = %s;
"""

SQL_GET_PENDING_AB_COMPARISON = """
SELECT comparison_id, conversation_id, user_prompt_mid, response_a_mid, response_b_mid,
       model_a, pipeline_a, model_b, pipeline_b,
       is_config_a_first, preference, preference_ts, created_at
FROM ab_comparisons
WHERE conversation_id = %s AND preference IS NULL
ORDER BY created_at DESC
LIMIT 1;
"""

SQL_DELETE_AB_COMPARISON = """
DELETE FROM ab_comparisons
WHERE comparison_id = %s;
"""

SQL_GET_AB_COMPARISONS_BY_CONVERSATION = """
SELECT comparison_id, conversation_id, user_prompt_mid, response_a_mid, response_b_mid,
       model_a, pipeline_a, model_b, pipeline_b,
       is_config_a_first, preference, preference_ts, created_at
FROM ab_comparisons
WHERE conversation_id = %s
ORDER BY created_at ASC;
"""

# =============================================================================
# Agent Trace Queries
# =============================================================================

SQL_CREATE_AGENT_TRACE = """
INSERT INTO agent_traces (
    trace_id, conversation_id, message_id, user_message_id, 
    config_id, pipeline_name, events, started_at, status
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
"""

SQL_UPDATE_AGENT_TRACE = """
UPDATE agent_traces
SET events = %s,
    completed_at = %s,
    status = %s,
    message_id = COALESCE(%s, message_id),
    total_tool_calls = %s,
    total_duration_ms = %s,
    cancelled_by = %s,
    cancellation_reason = %s
WHERE trace_id = %s;
"""

SQL_GET_AGENT_TRACE = """
SELECT trace_id, conversation_id, message_id, user_message_id,
       config_id, pipeline_name, events, started_at, completed_at,
       status, total_tool_calls, total_tokens_used, total_duration_ms,
       cancelled_by, cancellation_reason, created_at
FROM agent_traces
WHERE trace_id = %s;
"""

SQL_GET_TRACE_BY_MESSAGE = """
SELECT trace_id, conversation_id, message_id, user_message_id,
       config_id, pipeline_name, events, started_at, completed_at,
       status, total_tool_calls, total_tokens_used, total_duration_ms,
       cancelled_by, cancellation_reason, created_at
FROM agent_traces
WHERE message_id = %s;
"""

SQL_GET_ACTIVE_TRACE = """
SELECT trace_id, conversation_id, message_id, user_message_id,
       config_id, pipeline_name, events, started_at, status
FROM agent_traces
WHERE conversation_id = %s AND status = 'running'
ORDER BY started_at DESC
LIMIT 1;
"""

SQL_CANCEL_ACTIVE_TRACES = """
UPDATE agent_traces
SET status = 'cancelled',
    completed_at = %s,
    cancelled_by = %s,
    cancellation_reason = %s
WHERE conversation_id = %s AND status = 'running';
"""

# =============================================================================
# Service Alert Queries
# =============================================================================

SQL_INSERT_ALERT = """
INSERT INTO service_alerts (severity, message, description, created_by)
VALUES (%s, %s, %s, %s)
RETURNING id, severity, message, description, created_by, created_at, expires_at, active;
"""

SQL_SET_ALERT_EXPIRY = """
UPDATE service_alerts
SET expires_at = %s
WHERE id = %s;
"""

SQL_LIST_ALERTS = """
SELECT id, severity, message, description, created_by, created_at, expires_at, active
FROM service_alerts
ORDER BY created_at DESC;
"""

SQL_LIST_ACTIVE_BANNER_ALERTS = """
SELECT id, severity, message, description, created_by, created_at, expires_at
FROM (
    SELECT id, severity, message, description, created_by, created_at, expires_at
    FROM service_alerts
    WHERE active = TRUE AND (expires_at IS NULL OR expires_at > NOW())
    ORDER BY created_at DESC
    LIMIT 5
) latest
ORDER BY
    CASE severity WHEN 'alarm' THEN 0 WHEN 'warning' THEN 1 WHEN 'info' THEN 2 WHEN 'news' THEN 3 ELSE 4 END,
    created_at DESC;
"""

SQL_DELETE_ALERT = """
DELETE FROM service_alerts WHERE id = %s;
"""
