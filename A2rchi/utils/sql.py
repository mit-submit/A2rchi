"""SQL queries used by A2rchi"""
SQL_INSERT_CONVO = "INSERT INTO conversations (conversation_id, sender, content, ts) VALUES %s RETURNING message_id;"

SQL_INSERT_FEEDBACK = "INSERT INTO feedback (mid, feedback_ts, feedback, feedback_msg, incorrect, unhelpful, inappropriate) VALUES (%s, %s, %s, %s, %s, %s, %s);"

SQL_QUERY_CONVO = "SELECT sender, content FROM conversations WHERE conversation_id = %s ORDER BY message_id ASC;"
