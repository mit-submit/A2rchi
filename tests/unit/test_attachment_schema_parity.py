"""DDL parity: init.sql's conversation_attachments must match _SCHEMA_SQL.

The conversation_attachments table is created two independent ways — the
compose path runs src/cli/templates/init.sql, the Helm/service path runs
AttachmentService.ensure_schema() (which uses _SCHEMA_SQL) because Helm never
runs config-seed/init.sql. The two DDLs are duplicated on purpose; nothing
otherwise keeps them in sync. If they drift — e.g. a NOT NULL column added to
one only, or a NOT-NULL-no-default column added to both but not to
SQL_INSERT_ATTACHMENT — one deploy path silently rejects every insert while the
other keeps working, and psycopg2 is mocked in unit tests so no other test sees
it. This is a pure-string regression guard: no DB, no psycopg2 needed.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_SQL_PATH = _REPO_ROOT / "src" / "cli" / "templates" / "init.sql"


def _load_service_ddl():
    # Imported lazily/defensively: the service module is concurrently editable,
    # and a transient mid-edit state must not wedge test collection.
    from src.utils.attachment_service import SQL_INSERT_ATTACHMENT, _SCHEMA_SQL
    return _SCHEMA_SQL, SQL_INSERT_ATTACHMENT


def _paren_body(sql: str, opening_index: int) -> str:
    """Return the text inside the parens whose '(' is at opening_index."""
    depth = 0
    for i in range(opening_index, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[opening_index + 1 : i]
    raise AssertionError("unbalanced parentheses")


def _create_table_body(sql: str, table: str) -> str:
    m = re.search(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?" + re.escape(table) + r"\s*\(",
        sql,
        re.I,
    )
    assert m, f"no CREATE TABLE for {table} found"
    return _paren_body(sql, m.end() - 1)


def _insert_columns(insert_sql: str) -> list:
    m = re.search(
        r"insert\s+into\s+conversation_attachments\s*\(", insert_sql, re.I
    )
    assert m, "no INSERT INTO conversation_attachments found"
    body = _paren_body(insert_sql, m.end() - 1)
    return [c.strip().lower() for c in body.split(",") if c.strip()]


def _split_top_level_commas(body: str) -> list:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _normalize(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip().lower()


def _columns(body: str) -> list:
    """[(name, nullable, has_default), ...] in declaration order."""
    out = []
    for part in _split_top_level_commas(body):
        low = part.lower()
        name = re.match(r"\s*([a-z_][a-z0-9_]*)", low).group(1)
        not_null = "not null" in low
        primary_key = "primary key" in low
        has_default = "default" in low
        nullable = not (not_null or primary_key)
        out.append((name, nullable, has_default))
    return out


def test_init_sql_and_service_ddl_bodies_are_identical():
    schema_sql, _ = _load_service_ddl()
    init_body = _create_table_body(
        _INIT_SQL_PATH.read_text(), "conversation_attachments"
    )
    service_body = _create_table_body(schema_sql, "conversation_attachments")
    # Normalized column/constraint text must be byte-identical: any drift in a
    # column name, type, or constraint between the two deploy paths fails here.
    assert _normalize(init_body) == _normalize(service_body)


def test_column_names_types_and_nullability_match_in_order():
    schema_sql, _ = _load_service_ddl()
    init_cols = _columns(
        _create_table_body(_INIT_SQL_PATH.read_text(), "conversation_attachments")
    )
    service_cols = _columns(
        _create_table_body(schema_sql, "conversation_attachments")
    )
    assert init_cols == service_cols
    names = [c[0] for c in service_cols]
    assert names == [
        "attachment_id", "conversation_id", "owner", "message_id", "filename",
        "kind", "size_bytes", "extension", "original_bytes", "extracted_text",
        "extraction_meta", "created_at",
    ]


def test_insert_column_list_is_a_subset_and_omitted_columns_are_optional():
    schema_sql, insert_sql = _load_service_ddl()
    cols = _columns(_create_table_body(schema_sql, "conversation_attachments"))
    by_name = {name: (nullable, has_default) for name, nullable, has_default in cols}
    inserted = _insert_columns(insert_sql)

    # Every inserted column exists in the table.
    assert set(inserted) <= set(by_name)
    # Every column the insert omits must be safely omissible (nullable or has a
    # default); a future NOT-NULL-no-default column absent from the insert list
    # would break every insert on the compose path — catch it here.
    for name, (nullable, has_default) in by_name.items():
        if name not in inserted:
            assert nullable or has_default, (
                f"column {name!r} is omitted from SQL_INSERT_ATTACHMENT but is "
                f"neither nullable nor defaulted"
            )
