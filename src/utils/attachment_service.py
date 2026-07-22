"""AttachmentService — per-conversation attachment storage (spec 2026-07-06).

All bytes live in Postgres; the FK to conversation_metadata carries
ON DELETE CASCADE, which IS the privacy/deletion guarantee. Construction
mirrors ConversationService(connection_params=...). DDL lives here (not only
init.sql) because Helm deployments never run config-seed/init.sql on existing
databases — the service entrypoint must own its schema (playbooks lesson).
"""
from __future__ import annotations

import json
from typing import List, Optional

import psycopg2
from psycopg2.extras import Json

from src.utils.logging import get_logger
from src.utils.sql import (
    SQL_ATTACHMENT_FOR_TOOLS,
    SQL_GET_CONVERSATION_METADATA,
    SQL_GET_CONVERSATION_METADATA_BY_USER,
)

logger = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_attachments (
    attachment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id INTEGER NOT NULL
                    REFERENCES conversation_metadata(conversation_id) ON DELETE CASCADE,
    owner           TEXT NOT NULL,
    message_id      INTEGER,
    filename        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    size_bytes      BIGINT NOT NULL,
    extension       TEXT NOT NULL,
    original_bytes  BYTEA NOT NULL,
    extracted_text  TEXT NOT NULL,
    extraction_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_conv_attachments_conversation
    ON conversation_attachments(conversation_id);
"""

SQL_INSERT_ATTACHMENT = """
INSERT INTO conversation_attachments (
    conversation_id, owner, filename, kind, size_bytes, extension,
    original_bytes, extracted_text, extraction_meta
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING attachment_id, created_at;
"""

SQL_COUNT_ATTACHMENTS = """
SELECT COUNT(*) FROM conversation_attachments WHERE conversation_id = %s;
"""

SQL_SUM_BYTES_FOR_OWNER = """
SELECT COALESCE(SUM(size_bytes), 0) FROM conversation_attachments WHERE owner = %s;
"""

SQL_LIST_ATTACHMENTS = """
SELECT attachment_id, filename, kind, size_bytes, extension, message_id,
       created_at, extraction_meta
FROM conversation_attachments
WHERE conversation_id = %s
ORDER BY created_at ASC, attachment_id ASC;
"""

SQL_CONTEXT_ITEMS = """
SELECT filename, kind, extracted_text, extraction_meta, created_at
FROM conversation_attachments
WHERE conversation_id = %s
ORDER BY created_at ASC, attachment_id ASC;
"""

# Metadata-only variant for callers that never read extracted_text (the
# list/dedupe paths). extracted_text can be megabytes per row; selecting it for
# a listing streams it all out of Postgres for nothing.
SQL_CONTEXT_ITEMS_META = """
SELECT filename, kind, extraction_meta, created_at
FROM conversation_attachments
WHERE conversation_id = %s
ORDER BY created_at ASC, attachment_id ASC;
"""

SQL_DELETE_ATTACHMENT = """
DELETE FROM conversation_attachments a
USING conversation_metadata m
WHERE a.attachment_id = %s
  AND a.conversation_id = m.conversation_id
  AND m.client_id = %s
RETURNING a.attachment_id;
"""

SQL_DELETE_ATTACHMENT_BY_USER = """
DELETE FROM conversation_attachments a
USING conversation_metadata m
WHERE a.attachment_id = %s
  AND a.conversation_id = m.conversation_id
  AND (m.user_id = %s OR m.client_id = %s)
RETURNING a.attachment_id;
"""

SQL_BIND_ATTACHMENTS = """
UPDATE conversation_attachments
SET message_id = %s
WHERE conversation_id = %s AND message_id IS NULL;
"""

# Abandoned attach-to-new-chat cleanup: a conversation that only ever received
# an attachment (never a message). The FK ON DELETE CASCADE reclaims the
# attachment rows when the conversation_metadata row is deleted.
SQL_SWEEP_ABANDONED_CONVERSATIONS = """
DELETE FROM conversation_metadata m
WHERE m.created_at < now() - (%s * interval '1 hour')
  AND NOT EXISTS (
      SELECT 1 FROM conversations c WHERE c.conversation_id = m.conversation_id
  )
  AND EXISTS (
      SELECT 1 FROM conversation_attachments a WHERE a.conversation_id = m.conversation_id
  );
"""


def _strip_nuls(value):
    """Recursively remove NUL characters. psycopg2 raises an uncaught
    ValueError on a NUL byte in a string parameter, and Postgres JSONB
    rejects NUL bytes outright, so any client-controlled string (or string
    nested in a dict/list) must be scrubbed before it reaches the DB driver.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_strip_nuls(k): _strip_nuls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nuls(v) for v in value]
    return value


class AttachmentService:
    def __init__(self, connection_params: dict):
        self.connection_params = connection_params

    def _connect(self):
        return psycopg2.connect(**self.connection_params)

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(_SCHEMA_SQL)
            cursor.execute(_INDEX_SQL)
            conn.commit()
            logger.info("conversation_attachments schema ensured")
            cursor.close()
        finally:
            conn.close()

    def verify_conversation_access(
        self, conversation_id: int, client_id: str, user_id: Optional[str]
    ) -> bool:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute(
                    SQL_GET_CONVERSATION_METADATA_BY_USER,
                    (conversation_id, user_id, client_id),
                )
            else:
                cursor.execute(SQL_GET_CONVERSATION_METADATA, (conversation_id, client_id))
            row = cursor.fetchone()
            cursor.close()
            return row is not None
        finally:
            conn.close()

    def count_for_conversation(self, conversation_id: int) -> int:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(SQL_COUNT_ATTACHMENTS, (conversation_id,))
            (count,) = cursor.fetchone()
            cursor.close()
            return int(count)
        finally:
            conn.close()

    def bytes_for_owner(self, owner: str) -> int:
        """Total stored size (sum of size_bytes) across every attachment this
        owner holds, in any conversation. Backs the per-user storage quota."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(SQL_SUM_BYTES_FOR_OWNER, (owner,))
            (total,) = cursor.fetchone()
            cursor.close()
            return int(total or 0)
        finally:
            conn.close()

    def create_attachment(
        self, conversation_id: int, owner: str, filename: str, kind: str,
        size_bytes: int, extension: str, original_bytes: bytes,
        extracted_text: str, extraction_meta: dict,
    ) -> dict:
        filename = filename.replace("\x00", "")
        extraction_meta = _strip_nuls(extraction_meta)
        owner = owner.replace("\x00", "")
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                SQL_INSERT_ATTACHMENT,
                (
                    conversation_id, owner, filename, kind, size_bytes, extension,
                    psycopg2.Binary(original_bytes),
                    extracted_text.replace("\x00", ""),
                    Json(extraction_meta),
                ),
            )
            attachment_id, created_at = cursor.fetchone()
            conn.commit()
            cursor.close()
            return {
                "attachment_id": str(attachment_id),
                "created_at": created_at.isoformat() if created_at else None,
            }
        finally:
            conn.close()

    def list_for_conversation(self, conversation_id: int) -> List[dict]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(SQL_LIST_ATTACHMENTS, (conversation_id,))
            rows = cursor.fetchall()
            cursor.close()
            out = []
            for row in rows:
                meta = row[7]
                if isinstance(meta, str):
                    meta = json.loads(meta)
                out.append({
                    "attachment_id": str(row[0]),
                    "filename": row[1],
                    "kind": row[2],
                    "size_bytes": row[3],
                    "extension": row[4],
                    "message_id": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                    "extraction_meta": meta or {},
                })
            return out
        finally:
            conn.close()

    def get_context_items(self, conversation_id: int, include_text: bool = True) -> List[dict]:
        """Attachment items for a conversation. include_text=False skips the
        (potentially megabyte) extracted_text column for metadata-only callers
        (listing, duplicate-name checks); search/context-injection keep it."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if include_text:
                cursor.execute(SQL_CONTEXT_ITEMS, (conversation_id,))
            else:
                cursor.execute(SQL_CONTEXT_ITEMS_META, (conversation_id,))
            rows = cursor.fetchall()
            cursor.close()
            items = []
            for row in rows:
                if include_text:
                    filename, kind, extracted_text, meta, created_at = row
                else:
                    filename, kind, meta, created_at = row
                    extracted_text = ""
                if isinstance(meta, str):
                    meta = json.loads(meta)
                items.append({
                    "filename": filename,
                    "kind": kind,
                    "extracted_text": extracted_text,
                    "extraction_meta": meta or {},
                    "created_at": created_at,
                })
            return items
        finally:
            conn.close()

    def get_for_tools(self, conversation_id: int, filename: str) -> Optional[dict]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(SQL_ATTACHMENT_FOR_TOOLS, (conversation_id, filename))
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            meta = row[3]
            if isinstance(meta, str):
                meta = json.loads(meta)
            return {
                "filename": row[0],
                "kind": row[1],
                "extracted_text": row[2] or "",
                "extraction_meta": meta or {},
                "original_bytes": bytes(row[4]) if row[4] is not None else b"",
            }
        finally:
            conn.close()

    def delete_attachment(
        self, attachment_id: str, client_id: str, user_id: Optional[str]
    ) -> bool:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute(
                    SQL_DELETE_ATTACHMENT_BY_USER, (attachment_id, user_id, client_id)
                )
            else:
                cursor.execute(SQL_DELETE_ATTACHMENT, (attachment_id, client_id))
            deleted = cursor.fetchone() is not None
            conn.commit()
            cursor.close()
            if not deleted:
                logger.warning(
                    "Attachment delete denied or not found: %s (client %s)",
                    attachment_id, client_id,
                )
            return deleted
        finally:
            conn.close()

    def bind_unbound_to_message(self, conversation_id: int, message_id: int) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(SQL_BIND_ATTACHMENTS, (message_id, conversation_id))
            conn.commit()
            cursor.close()
        finally:
            conn.close()

    def sweep_abandoned_conversations(self, ttl_hours: int) -> int:
        """Delete message-less conversations that hold at least one attachment
        and are older than ttl_hours (attach-to-new-chat, then abandoned or the
        file removed before sending). Returns the number of conversations
        deleted; their attachments cascade away. ttl_hours <= 0 is a no-op."""
        if ttl_hours <= 0:
            return 0
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(SQL_SWEEP_ABANDONED_CONVERSATIONS, (ttl_hours,))
            swept = cursor.rowcount
            conn.commit()
            cursor.close()
            return swept
        finally:
            conn.close()
