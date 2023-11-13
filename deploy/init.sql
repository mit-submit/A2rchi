-- create tables
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id INTEGER NOT NULL,
    message_id SERIAL,
    sender TEXT NOT NULL,
    content TEXT NOT NULL,
    ts TIMESTAMP NOT NULL,
    PRIMARY KEY (message_id)
);
CREATE TABLE IF NOT EXISTS feedback (
    mid INTEGER NOT NULL,
    feedback_ts TIMESTAMP NOT NULL,
    feedback TEXT NOT NULL,
    feedback_msg TEXT,
    incorrect BOOLEAN,
    unhelpful BOOLEAN,
    inappropriate BOOLEAN,
    PRIMARY KEY (mid, feedback_ts),
    FOREIGN KEY (mid) REFERENCES conversations(message_id)
);
CREATE TABLE IF NOT EXISTS timing (
    mid INTEGER NOT NULL,
    client_sent_msg_ts TIMESTAMP NOT NULL,
    server_received_msg_ts TIMESTAMP NOT NULL,
    lock_acquisition_ts TIMESTAMP NOT NULL,
    vectorstore_update_ts TIMESTAMP NOT NULL,
    query_convo_history_ts TIMESTAMP NOT NULL,
    chain_finished_ts TIMESTAMP NOT NULL,
    similarity_search_ts TIMESTAMP NOT NULL,
    a2rchi_message_ts TIMESTAMP NOT NULL,
    insert_convo_ts TIMESTAMP NOT NULL,
    finish_call_ts TIMESTAMP NOT NULL,
    server_response_msg_ts TIMESTAMP NOT NULL,
    msg_duration INTERVAL SECOND NOT NULL,
    PRIMARY KEY (mid),
    FOREIGN KEY (mid) REFERENCES conversations(message_id)
);

-- create grafana user if it does not exist
DO
$do$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana') THEN
        CREATE USER grafana WITH PASSWORD 'GRAFANA_PASSWORD';
        GRANT USAGE ON SCHEMA public TO grafana;
        GRANT SELECT ON public.timing TO grafana;
        GRANT SELECT ON public.conversations TO grafana;
        GRANT SELECT ON public.feedback TO grafana;
    END IF;
END
$do$;

