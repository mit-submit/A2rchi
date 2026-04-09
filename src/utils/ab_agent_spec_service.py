"""
Database-backed A/B agent spec catalog and versioning.

This service replaces file-backed storage for the A/B-only agent catalog while
keeping the default chat agent workflow unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from src.archi.pipelines.agents.agent_spec import (
    AgentSpec,
    AgentSpecError,
    list_agent_files,
    load_agent_spec,
    load_agent_spec_from_text,
    slugify_agent_name,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ABAgentSpecRecord:
    spec_id: int
    filename: str
    name: str
    content: str
    tools: List[str]
    prompt: str
    ab_only: bool
    version_id: int
    version_number: int
    content_hash: str
    prompt_hash: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    source_type: Optional[str] = None
    source_path: Optional[str] = None

    def to_agent_spec(self) -> AgentSpec:
        return AgentSpec(
            name=self.name,
            tools=list(self.tools),
            prompt=self.prompt,
            source_path=Path(f"<ab-db:{self.filename}>"),
            ab_only=self.ab_only,
        )


class ABAgentSpecService:
    """Persist and version A/B-only agent specs in PostgreSQL."""

    def __init__(
        self,
        pg_config: Optional[Dict[str, Any]] = None,
        *,
        connection_pool=None,
    ) -> None:
        self._pool = connection_pool
        self._pg_config = pg_config
        self._ensure_tables()

    def _get_connection(self) -> psycopg2.extensions.connection:
        if self._pool:
            if hasattr(self._pool, "get_connection_direct"):
                return self._pool.get_connection_direct()
            return self._pool.get_connection()
        if self._pg_config:
            return psycopg2.connect(**self._pg_config)
        raise ValueError("No connection pool or pg_config provided")

    def _release_connection(self, conn) -> None:
        if self._pool and hasattr(self._pool, "release_connection"):
            self._pool.release_connection(conn)
        else:
            conn.close()

    def _ensure_tables(self) -> None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ab_agent_specs (
                        spec_id SERIAL PRIMARY KEY,
                        filename VARCHAR(255) NOT NULL UNIQUE,
                        current_name VARCHAR(255) NOT NULL UNIQUE,
                        current_version_id INTEGER,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_saved_by VARCHAR(200)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ab_agent_spec_versions (
                        version_id SERIAL PRIMARY KEY,
                        spec_id INTEGER NOT NULL REFERENCES ab_agent_specs(spec_id) ON DELETE CASCADE,
                        version_number INTEGER NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        tools TEXT[] NOT NULL DEFAULT '{}',
                        prompt TEXT NOT NULL,
                        content TEXT NOT NULL,
                        ab_only BOOLEAN NOT NULL DEFAULT FALSE,
                        content_hash VARCHAR(64) NOT NULL,
                        prompt_hash VARCHAR(64) NOT NULL,
                        source_type VARCHAR(50) NOT NULL DEFAULT 'ui',
                        source_path TEXT,
                        created_by VARCHAR(200),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (spec_id, version_number)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_ab_agent_spec_versions_spec
                    ON ab_agent_spec_versions(spec_id, version_number DESC)
                    """
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _prompt_hash(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_record(row: Dict[str, Any]) -> ABAgentSpecRecord:
        return ABAgentSpecRecord(
            spec_id=row["spec_id"],
            filename=row["filename"],
            name=row["name"],
            content=row["content"],
            tools=list(row["tools"] or []),
            prompt=row["prompt"],
            ab_only=bool(row["ab_only"]),
            version_id=row["version_id"],
            version_number=row["version_number"],
            content_hash=row["content_hash"],
            prompt_hash=row["prompt_hash"],
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
            source_type=row.get("source_type"),
            source_path=row.get("source_path"),
        )

    def _get_by_clause(self, clause: str, value: str) -> Optional[ABAgentSpecRecord]:
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    f"""
                    SELECT s.spec_id,
                           s.filename,
                           s.current_name,
                           s.created_at,
                           s.updated_at,
                           v.version_id,
                           v.version_number,
                           v.name,
                           v.tools,
                           v.prompt,
                           v.content,
                           v.ab_only,
                           v.content_hash,
                           v.prompt_hash,
                           v.source_type,
                           v.source_path
                    FROM ab_agent_specs s
                    JOIN ab_agent_spec_versions v ON v.version_id = s.current_version_id
                    WHERE {clause} = %s
                    """,
                    (value,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)
        finally:
            self._release_connection(conn)

    def list_specs(self) -> List[ABAgentSpecRecord]:
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT s.spec_id,
                           s.filename,
                           s.current_name,
                           s.created_at,
                           s.updated_at,
                           v.version_id,
                           v.version_number,
                           v.name,
                           v.tools,
                           v.prompt,
                           v.content,
                           v.ab_only,
                           v.content_hash,
                           v.prompt_hash,
                           v.source_type,
                           v.source_path
                    FROM ab_agent_specs s
                    JOIN ab_agent_spec_versions v ON v.version_id = s.current_version_id
                    ORDER BY lower(v.name), lower(s.filename)
                    """
                )
                return [self._row_to_record(row) for row in cursor.fetchall()]
        finally:
            self._release_connection(conn)

    def get_spec_by_name(self, name: str) -> Optional[ABAgentSpecRecord]:
        return self._get_by_clause("s.current_name", name.strip())

    def get_spec_by_filename(self, filename: str) -> Optional[ABAgentSpecRecord]:
        return self._get_by_clause("s.filename", filename.strip())

    def spec_exists(self, filename: str) -> bool:
        return self.get_spec_by_filename(filename) is not None

    def load_agent_spec(self, filename: str) -> ABAgentSpecRecord:
        record = self.get_spec_by_filename(filename)
        if record is None:
            raise AgentSpecError(f"A/B agent spec '{filename}' not found in database")
        return record

    def _next_available_filename(self, cursor, base_name: str) -> str:
        filename = slugify_agent_name(base_name)
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        candidate = filename
        counter = 2
        while True:
            cursor.execute("SELECT 1 FROM ab_agent_specs WHERE filename = %s", (candidate,))
            if cursor.fetchone() is None:
                return candidate
            candidate = f"{stem}-{counter}{suffix}"
            counter += 1

    def _insert_version(
        self,
        cursor,
        *,
        spec_id: int,
        version_number: int,
        spec,
        content: str,
        content_hash: str,
        prompt_hash: str,
        source_type: str,
        source_path: Optional[str],
        created_by: Optional[str],
    ) -> int:
        cursor.execute(
            """
            INSERT INTO ab_agent_spec_versions (
                spec_id,
                version_number,
                name,
                tools,
                prompt,
                content,
                ab_only,
                content_hash,
                prompt_hash,
                source_type,
                source_path,
                created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING version_id
            """,
            (
                spec_id,
                version_number,
                spec.name,
                spec.tools,
                spec.prompt,
                content,
                bool(spec.ab_only),
                content_hash,
                prompt_hash,
                source_type,
                source_path,
                created_by,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise AgentSpecError("Failed to create A/B agent spec version")
        if isinstance(row, dict):
            version_id = row.get("version_id")
        else:
            try:
                version_id = row[0]
            except (TypeError, KeyError, IndexError):
                version_id = getattr(row, "version_id", None)
        if version_id is None:
            raise AgentSpecError("Failed to read A/B agent spec version id")
        return int(version_id)

    def save_spec(
        self,
        content: str,
        *,
        existing_name: Optional[str] = None,
        created_by: Optional[str] = None,
        source_type: str = "ui",
        source_path: Optional[str] = None,
    ) -> ABAgentSpecRecord:
        spec = load_agent_spec_from_text(content)
        content_hash = self._content_hash(content)
        prompt_hash = self._prompt_hash(spec.prompt)

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                if existing_name:
                    current = self.get_spec_by_name(existing_name)
                    if current is None:
                        raise AgentSpecError(f"A/B agent '{existing_name}' not found")
                    if spec.name != current.name:
                        raise AgentSpecError(
                            "Agent name cannot be changed in edit mode. Create a new A/B agent instead."
                        )
                    if content_hash == current.content_hash:
                        cursor.execute(
                            """
                            UPDATE ab_agent_specs
                            SET updated_at = NOW(), last_saved_by = %s
                            WHERE spec_id = %s
                            """,
                            (created_by, current.spec_id),
                        )
                        conn.commit()
                        return self.get_spec_by_name(existing_name)

                    next_version = current.version_number + 1
                    version_id = self._insert_version(
                        cursor,
                        spec_id=current.spec_id,
                        version_number=next_version,
                        spec=spec,
                        content=content,
                        content_hash=content_hash,
                        prompt_hash=prompt_hash,
                        source_type=source_type,
                        source_path=source_path,
                        created_by=created_by,
                    )
                    cursor.execute(
                        """
                        UPDATE ab_agent_specs
                        SET current_version_id = %s,
                            current_name = %s,
                            updated_at = NOW(),
                            last_saved_by = %s
                        WHERE spec_id = %s
                        """,
                        (version_id, spec.name, created_by, current.spec_id),
                    )
                    conn.commit()
                    return self.get_spec_by_name(existing_name)

                cursor.execute("SELECT 1 FROM ab_agent_specs WHERE current_name = %s", (spec.name,))
                if cursor.fetchone() is not None:
                    raise AgentSpecError(f"A/B agent name '{spec.name}' already exists")

                filename = self._next_available_filename(cursor, spec.name)
                cursor.execute(
                    """
                    INSERT INTO ab_agent_specs (filename, current_name, last_saved_by)
                    VALUES (%s, %s, %s)
                    RETURNING spec_id
                    """,
                    (filename, spec.name, created_by),
                )
                spec_id = int(cursor.fetchone()["spec_id"])
                version_id = self._insert_version(
                    cursor,
                    spec_id=spec_id,
                    version_number=1,
                    spec=spec,
                    content=content,
                    content_hash=content_hash,
                    prompt_hash=prompt_hash,
                    source_type=source_type,
                    source_path=source_path,
                    created_by=created_by,
                )
                cursor.execute(
                    """
                    UPDATE ab_agent_specs
                    SET current_version_id = %s, updated_at = NOW()
                    WHERE spec_id = %s
                    """,
                    (version_id, spec_id),
                )
                conn.commit()
                return self.get_spec_by_filename(filename)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    def delete_spec_by_name(self, name: str) -> bool:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM ab_agent_specs WHERE current_name = %s", (name.strip(),))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    def import_directory(
        self,
        directory: Path,
        *,
        created_by: Optional[str] = "system",
    ) -> Dict[str, Any]:
        result = {
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "conflicts": [],
        }
        directory = Path(directory).expanduser()
        if not directory.exists() or not directory.is_dir():
            return result

        try:
            files = list_agent_files(directory)
        except AgentSpecError:
            return result

        for path in files:
            try:
                record = self.get_spec_by_filename(path.name)
                disk_content = path.read_text()
                disk_hash = self._content_hash(disk_content)
                file_spec = load_agent_spec(path)

                if record is not None:
                    if record.name != file_spec.name:
                        result["conflicts"].append(
                            f"{path.name} maps to '{record.name}' in DB but '{file_spec.name}' on disk"
                        )
                        continue
                    if record.content_hash == disk_hash:
                        result["skipped"] += 1
                        continue
                    self.save_spec(
                        disk_content,
                        existing_name=record.name,
                        created_by=created_by,
                        source_type="import",
                        source_path=str(path),
                    )
                    result["updated"] += 1
                    continue

                existing_by_name = self.get_spec_by_name(file_spec.name)
                if existing_by_name is not None and existing_by_name.filename != path.name:
                    result["conflicts"].append(
                        f"Name '{file_spec.name}' already exists in DB as '{existing_by_name.filename}'"
                    )
                    continue

                self.save_spec(
                    disk_content,
                    created_by=created_by,
                    source_type="import",
                    source_path=str(path),
                )
                created = self.get_spec_by_name(file_spec.name)
                if created and created.filename != path.name:
                    conn = self._get_connection()
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute(
                                """
                                UPDATE ab_agent_specs
                                SET filename = %s, updated_at = NOW()
                                WHERE spec_id = %s
                                """,
                                (path.name, created.spec_id),
                            )
                            conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                    finally:
                        self._release_connection(conn)
                result["imported"] += 1
            except Exception as exc:
                logger.warning("Failed to import A/B agent spec %s: %s", path, exc)
                result["conflicts"].append(f"{path.name}: {exc}")

        return result
