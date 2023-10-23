CREATE TABLE IF NOT EXISTS conversations (
    conversation_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sender TEXT NOT NULL, -- eventually put foreign key constraint on users table
    content TEXT NOT NULL,
    ts TIMESTAMP NOT NULL,
    PRIMARY KEY (conversation_id, message_id)
);
CREATE TABLE IF NOT EXISTS feedback (
    cid INTEGER NOT NULL,
    mid INTEGER NOT NULL,
    feedback_ts TIMESTAMP NOT NULL,
    feedback TEXT NOT NULL,
    message TEXT,
    incorrect BOOLEAN,
    unhelpful BOOLEAN,
    inappropriate BOOLEAN,
    PRIMARY KEY (cid, mid, feedback_ts),
    FOREIGN KEY (cid, mid) REFERENCES conversations(conversation_id, message_id) 
);