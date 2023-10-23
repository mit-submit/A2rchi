"""SQL queries used by A2rchi"""
SQL_INSERT_CONVO = "INSERT INTO conversations (conversation_id, sender, content, ts) VALUES %s RETURNING message_id;"

SQL_INSERT_FEEDBACK = "INSERT INTO feedback (cid, mid, feedback_ts, feedback, message, incorrect, unhelpful, inappropriate) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);"
