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
    FOREIGN KEY mid REFERENCES conversations(message_id)
);