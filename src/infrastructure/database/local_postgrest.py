"""
LocalPostgrestShim (issue #465, part of #462 local-first).

`SupabaseManager` speaks PostgREST — every Supabase-only subsystem calls it with
PostgREST-style verbs (`query/insert/update/upsert/delete/rpc`) and filters
(`{"id": "eq.5"}`, `{"or": "(a.eq.1,b.is.null)"}`, `order="created_at.desc"`).
When Supabase isn't configured, those verbs used to silently no-op.

This shim makes the disabled path delegate to a local SQLite store instead, so the
*same* repository code works offline with zero setup — the exact fallback pattern
`memory_repository.py` hand-rolls, generalized once. It translates the PostgREST
filter grammar into parameterized SQL and preserves the manager's return shapes
exactly (`list[dict]` for query, `dict | None` for insert/upsert, `bool` for
update/delete).

Backing tables live in the same `data/local_chad.db` as `local_store` and are
created lazily. Each consumer slice declares its table once via `register_table`
so the shim never needs per-feature edits. `rpc` raises `NotImplementedError`
unless a handler is registered (the OATS vector-match slice registers its own).
"""

import json
import logging
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Sequence, Union

from src.infrastructure.database import local_store

logger = logging.getLogger("LocalPostgrestShim")

# Comparison operators that map to a single SQL binary operator.
_BINARY_OPS = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}

ColumnSpec = Union[Sequence[str], Dict[str, str]]


class LocalPostgrestShim:
    """SQLite-backed stand-in for PostgREST, driven by a registered schema map."""

    def __init__(self) -> None:
        # name -> {"columns": {col: sqltype}, "pk": [cols], "json": {cols}}
        self._tables: Dict[str, Dict[str, Any]] = {}
        self._created: set = set()
        self._rpc: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # --- registration -------------------------------------------------------

    def register_table(
        self,
        name: str,
        columns: ColumnSpec,
        pk: Union[str, Sequence[str]],
        json_columns: Optional[Sequence[str]] = None,
    ) -> None:
        """Declare a backing table. Idempotent — safe to call from every consumer __init__.

        `columns` is either a list of column names (all TEXT) or a dict mapping
        name -> SQL type (`TEXT`, `INTEGER`, `REAL`, `BLOB`, or `JSON`). `JSON`
        columns are stored as TEXT and transparently serialized on write / parsed
        on read. `pk` is the primary-key column name (or a list for composite keys).
        """
        if isinstance(columns, dict):
            coldefs: Dict[str, str] = {c: (t or "TEXT").upper() for c, t in columns.items()}
        else:
            coldefs = {c: "TEXT" for c in columns}

        pk_list = [pk] if isinstance(pk, str) else list(pk)
        # A pk column that wasn't described defaults to an autoincrementing integer id.
        for col in pk_list:
            coldefs.setdefault(col, "INTEGER")

        json_set = {c for c, t in coldefs.items() if t == "JSON"}
        if json_columns:
            json_set.update(json_columns)

        with self._lock:
            self._tables[name] = {"columns": coldefs, "pk": pk_list, "json": json_set}
            # Force a re-create check in case a schema was re-registered mid-process.
            self._created.discard(name)

    def register_rpc(self, name: str, handler) -> None:
        """Register a local handler for `rpc(name, params)` (e.g. OATS vector match)."""
        self._rpc[name] = handler

    # --- connection / schema ------------------------------------------------

    @staticmethod
    def _connect() -> sqlite3.Connection:
        conn = sqlite3.connect(local_store.get_local_db_path(), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure(self, table: str) -> Dict[str, str]:
        """Lazily CREATE the table on first use; return its {col: type} map."""
        spec = self._tables.get(table)
        if spec is None:
            raise KeyError(
                f"table '{table}' is not registered with LocalPostgrestShim — "
                f"call register_table('{table}', columns, pk) first"
            )
        if table not in self._created:
            with self._lock:
                if table not in self._created:
                    conn = self._connect()
                    try:
                        conn.execute(self._create_sql(table))
                        conn.commit()
                    finally:
                        conn.close()
                    self._created.add(table)
        return spec["columns"]

    def _create_sql(self, table: str) -> str:
        spec = self._tables[table]
        cols: Dict[str, str] = spec["columns"]
        pk: List[str] = spec["pk"]
        defs = []
        for col, typ in cols.items():
            sqltype = "TEXT" if typ == "JSON" else typ
            if len(pk) == 1 and col == pk[0]:
                defs.append(f"{col} {sqltype} PRIMARY KEY")
            else:
                defs.append(f"{col} {sqltype}")
        if len(pk) > 1:
            defs.append(f"PRIMARY KEY ({', '.join(pk)})")
        return f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(defs)})"

    def _ensure_unique_index(self, conn: sqlite3.Connection, table: str, cols: Sequence[str]) -> None:
        """ON CONFLICT needs a unique index on the conflict target; create one lazily."""
        idx = f"ux_{table}_{'_'.join(cols)}"
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {idx} ON {table} ({', '.join(cols)})")

    # --- verbs --------------------------------------------------------------

    def query(
        self,
        table: str,
        select: str = "*",
        filters: Optional[Dict[str, str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        types = self._ensure(table)
        projection = select if (select and select != "*") else "*"
        sql = f"SELECT {projection} FROM {table}"
        params: List[Any] = []
        if filters:
            where, wp = self._build_where(types, filters)
            sql += f" WHERE {where}"
            params += wp
        if order:
            sql += " ORDER BY " + self._build_order(order)
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
            if offset:
                sql += " OFFSET ?"
                params.append(int(offset))
        elif offset:
            # SQLite requires a LIMIT before OFFSET; -1 means "no limit".
            sql += " LIMIT -1 OFFSET ?"
            params.append(int(offset))

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_out(table, r) for r in rows]
        except Exception as e:
            logger.error(f"Local query failed on {table}: {e}")
            return []
        finally:
            conn.close()

    def insert(self, table: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self._ensure(table)
        cols = list(data.keys())
        vals = [self._encode(table, c, data[c]) for c in cols]
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        conn = self._connect()
        try:
            cur = conn.execute(sql, vals)
            conn.commit()
            row = conn.execute(
                f"SELECT * FROM {table} WHERE rowid = ?", (cur.lastrowid,)
            ).fetchone()
            return self._row_out(table, row) if row else None
        except Exception as e:
            logger.error(f"Local insert failed on {table}: {e}")
            return None
        finally:
            conn.close()

    def upsert(
        self, table: str, data: Dict[str, Any], on_conflict: str = "name"
    ) -> Optional[Dict[str, Any]]:
        spec = self._tables.get(table)
        self._ensure(table)
        conflict_cols = (
            [c.strip() for c in on_conflict.split(",")]
            if on_conflict
            else list(spec["pk"])
        )
        cols = list(data.keys())
        vals = [self._encode(table, c, data[c]) for c in cols]
        placeholders = ", ".join(["?"] * len(cols))
        update_cols = [c for c in cols if c not in conflict_cols]
        if update_cols:
            set_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
            action = f"DO UPDATE SET {set_clause}"
        else:
            action = "DO NOTHING"
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({', '.join(conflict_cols)}) {action}"
        )
        conn = self._connect()
        try:
            self._ensure_unique_index(conn, table, conflict_cols)
            conn.execute(sql, vals)
            conn.commit()
            where = " AND ".join(f"{c} = ?" for c in conflict_cols)
            wp = [self._encode(table, c, data.get(c)) for c in conflict_cols]
            row = conn.execute(f"SELECT * FROM {table} WHERE {where}", wp).fetchone()
            return self._row_out(table, row) if row else None
        except Exception as e:
            logger.error(f"Local upsert failed on {table}: {e}")
            return None
        finally:
            conn.close()

    def update(self, table: str, filters: Dict[str, str], data: Dict[str, Any]) -> bool:
        types = self._ensure(table)
        set_clause = ", ".join(f"{c} = ?" for c in data)
        params: List[Any] = [self._encode(table, c, data[c]) for c in data]
        where, wp = self._build_where(types, filters or {})
        params += wp
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        conn = self._connect()
        try:
            conn.execute(sql, params)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Local update failed on {table}: {e}")
            return False
        finally:
            conn.close()

    def delete(self, table: str, filters: Dict[str, str]) -> bool:
        types = self._ensure(table)
        where, wp = self._build_where(types, filters or {})
        sql = f"DELETE FROM {table} WHERE {where}"
        conn = self._connect()
        try:
            conn.execute(sql, wp)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Local delete failed on {table}: {e}")
            return False
        finally:
            conn.close()

    def rpc(self, function_name: str, params: Dict[str, Any]) -> Any:
        handler = self._rpc.get(function_name)
        if handler is None:
            raise NotImplementedError(
                f"rpc('{function_name}') has no local handler registered — "
                f"register one with register_rpc('{function_name}', handler)"
            )
        return handler(params)

    # --- filter-grammar translation ----------------------------------------

    def _build_where(self, types: Dict[str, str], filters: Dict[str, str]):
        clauses: List[str] = []
        params: List[Any] = []
        for key, val in filters.items():
            if key == "or":
                sub_sql, sub_params = self._build_or(types, val)
                clauses.append(sub_sql)
                params += sub_params
            else:
                c, p = self._translate(types, key, val)
                clauses.append(c)
                params += p
        return (" AND ".join(clauses) if clauses else "1=1"), params

    def _build_or(self, types: Dict[str, str], expr: str):
        """Translate a PostgREST `or=(a.eq.1,b.is.null,...)` clause."""
        inner = expr.strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1]
        sub_clauses: List[str] = []
        params: List[Any] = []
        for element in self._split_top_commas(inner):
            col, _, rest = element.strip().partition(".")
            c, p = self._translate(types, col, rest)
            sub_clauses.append(c)
            params += p
        joined = " OR ".join(sub_clauses) if sub_clauses else "1=1"
        return f"({joined})", params

    def _translate(self, types: Dict[str, str], col: str, expr: str):
        """Translate a single `col`/`op.value` pair into (sql_fragment, params)."""
        op, _, raw = expr.partition(".")
        colsql = self._col_ref(col)

        if op == "is":
            token = raw.strip().lower()
            if token == "null":
                return f"{colsql} IS NULL", []
            if token in ("not.null", "notnull"):
                return f"{colsql} IS NOT NULL", []
            if token == "true":
                return f"{colsql} = 1", []
            if token == "false":
                return f"{colsql} = 0", []
            return f"{colsql} IS ?", [raw]

        if op == "in":
            body = raw.strip()
            if body.startswith("(") and body.endswith(")"):
                body = body[1:-1]
            items = [self._unquote(x) for x in self._split_top_commas(body)] if body.strip() else []
            coerced = [self._coerce(types, col, x) for x in items]
            placeholders = ", ".join(["?"] * len(coerced)) if coerced else "NULL"
            return f"{colsql} IN ({placeholders})", coerced

        if op == "ilike":
            # PostgREST uses `*` as the wildcard; SQLite LIKE is case-insensitive.
            return f"{colsql} LIKE ?", [raw.replace("*", "%")]

        if op == "like":
            # Case-sensitive: SQLite GLOB already uses `*` as its wildcard.
            return f"{colsql} GLOB ?", [raw]

        if op in _BINARY_OPS:
            return f"{colsql} {_BINARY_OPS[op]} ?", [self._coerce(types, col, raw)]

        raise ValueError(f"unsupported PostgREST operator '{op}' in filter '{col}={expr}'")

    @staticmethod
    def _col_ref(col: str) -> str:
        """Resolve a column reference, translating PostgREST JSON paths (`meta->>key`)."""
        if "->>" in col:
            base, _, key = col.partition("->>")
            return f"json_extract({base}, '$.{key}')"
        return col

    @staticmethod
    def _coerce(types: Dict[str, str], col: str, value: str):
        """Coerce a string filter value to the column's numeric type.

        SQLite's affinity rules are unreliable for IN-lists and cross-type
        comparisons, so we convert eagerly based on the registered column type
        rather than trusting the engine to match `'5'` against integer `5`.

        PostgREST booleans (`eq.true`/`eq.false`) are coerced to 0/1 regardless
        of the registered type, since Python's sqlite3 driver stores bools that
        way and `BOOLEAN` columns carry SQLite's NUMERIC affinity.
        """
        typ = types.get(col)
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return 1 if value.strip().lower() == "true" else 0
        try:
            if typ == "INTEGER":
                return int(value)
            if typ == "REAL":
                return float(value)
        except (TypeError, ValueError):
            return value
        return value

    @staticmethod
    def _unquote(value: str) -> str:
        v = value.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            return v[1:-1]
        return v

    @staticmethod
    def _split_top_commas(s: str) -> List[str]:
        """Split on commas that are not nested inside parentheses."""
        if not s.strip():
            return []
        out: List[str] = []
        depth = 0
        cur = ""
        for ch in s:
            if ch == "(":
                depth += 1
                cur += ch
            elif ch == ")":
                depth -= 1
                cur += ch
            elif ch == "," and depth == 0:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        out.append(cur)
        return out

    @staticmethod
    def _build_order(order: str) -> str:
        parts = []
        for token in order.split(","):
            token = token.strip()
            if not token:
                continue
            head, _, tail = token.rpartition(".")
            if head and tail.lower() in ("asc", "desc"):
                parts.append(f"{head} {tail.upper()}")
            else:
                parts.append(f"{token} ASC")
        return ", ".join(parts)

    # --- row (de)serialization ---------------------------------------------

    def _encode(self, table: str, col: str, value: Any) -> Any:
        """Serialize a write value — JSON columns and dict/list values become text."""
        if value is None:
            return None
        json_cols = self._tables[table]["json"]
        if col in json_cols or isinstance(value, (dict, list)):
            return json.dumps(value)
        return value

    def _row_out(self, table: str, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a sqlite Row to a plain dict, parsing registered JSON columns."""
        d = dict(row)
        for col in self._tables[table]["json"]:
            if col in d and d[col] is not None:
                try:
                    d[col] = json.loads(d[col])
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        return d


# --- module-level singleton -------------------------------------------------

_shim_instance: Optional[LocalPostgrestShim] = None
_shim_lock = threading.Lock()


def get_local_shim() -> LocalPostgrestShim:
    """Return the process-wide shim, shared by SupabaseManager and every consumer."""
    global _shim_instance
    if _shim_instance is None:
        with _shim_lock:
            if _shim_instance is None:
                _shim_instance = LocalPostgrestShim()
    return _shim_instance


def register_table(
    name: str,
    columns: ColumnSpec,
    pk: Union[str, Sequence[str]],
    json_columns: Optional[Sequence[str]] = None,
) -> None:
    """Declare a backing table on the shared shim (see LocalPostgrestShim.register_table)."""
    get_local_shim().register_table(name, columns, pk, json_columns)


def register_rpc(name: str, handler) -> None:
    """Register a local `rpc` handler on the shared shim."""
    get_local_shim().register_rpc(name, handler)
