
import os
import httpx
import logging
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from src.shared.paths import get_project_root

logger = logging.getLogger("SupabaseManager")

class SupabaseManager:
    """
    Centralized manager for Supabase PostgREST interactions.
    Uses anon key for client-side (Brain) interactions.
    Uses synchronous httpx.Client to avoid sniffio/asyncio task-context issues
    in Python 3.14 + uvicorn background tasks.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SupabaseManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        load_dotenv(dotenv_path=str(get_project_root() / ".env"))
        self.url = os.getenv("SUPABASE_URL")
        self.key = os.getenv("SUPABASE_ANON_KEY")

        # Service-role key bypasses RLS for all Brain-side writes.
        # Falls back to anon key if not set (single-user / dev setups).
        self.service_key = (
            os.getenv("SUPABASE_SERVICE_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or self.key
        )

        if not self.url or not self.key:
            # Local-first mode (issue #462): unconfigured is a normal, fully
            # supported path — every verb routes through LocalPostgrestShim
            # instead — not an error condition worth logging as one.
            logger.info("SUPABASE_URL not set — running local-first (LocalPostgrestShim)")
            self.enabled = False
            self._client = None
        else:
            self.enabled = True
            self.base_url = f"{self.url}/rest/v1"
            # Use service_role key for all server-side operations so RLS is bypassed.
            # The Brain acts as a trusted backend — per-user data is scoped by
            # application-layer owner_id filtering, with RLS as a DB-layer safety net.
            self.headers = {
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            # Sync client: safe to create at any time, no event loop required
            self._client = httpx.Client(timeout=15.0)

        self._initialized = True

    def _local(self):
        """The SQLite-backed PostgREST shim used when Supabase isn't configured (issue #465)."""
        from src.infrastructure.database.local_postgrest import get_local_shim
        return get_local_shim()

    def query(self, table: str, select: str = "*", filters: Dict[str, str] = None, limit: int = None, offset: int = None, order: str = None) -> List[Dict[str, Any]]:
        """Perform a GET query on a table (sync)."""
        if not self.enabled:
            return self._local().query(table, select=select, filters=filters, limit=limit, offset=offset, order=order)

        url = f"{self.base_url}/{table}?select={select}"
        if filters:
            for k, v in filters.items():
                url += f"&{k}={v}"
        if limit:
            url += f"&limit={limit}"
        if offset:
            url += f"&offset={offset}"
        if order:
            url += f"&order={order}"

        from src.shared.benchmark import timer
        with timer(f"Supabase GET {table}"):
            try:
                response = self._client.get(url, headers=self.headers)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.warning(f"Supabase query failed on {table} ({e}); falling back to local SQLite")
                return self._local().query(table, select=select, filters=filters, limit=limit, offset=offset, order=order)

    async def async_query(self, table: str, select: str = "*", filters: Dict[str, str] = None, limit: int = None, offset: int = None, order: str = None) -> List[Dict[str, Any]]:
        """Perform a GET query on a table (async wrapper)."""
        import asyncio
        return await asyncio.to_thread(self.query, table, select, filters, limit, offset, order)

    def insert(self, table: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a row into a table (sync)."""
        if not self.enabled:
            return self._local().insert(table, data)

        from src.shared.benchmark import timer
        with timer(f"Supabase POST {table}"):
            try:
                response = self._client.post(f"{self.base_url}/{table}", headers=self.headers, json=data)
                response.raise_for_status()
                result = response.json()
                return result[0] if result else None
            except Exception as e:
                logger.warning(f"Supabase insert failed on {table} ({e}); falling back to local SQLite")
                return self._local().insert(table, data)

    async def async_insert(self, table: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a row into a table (async wrapper)."""
        import asyncio
        return await asyncio.to_thread(self.insert, table, data)

    def upsert(self, table: str, data: Dict[str, Any], on_conflict: str = "name") -> Optional[Dict[str, Any]]:
        """Upsert a row into a table (sync)."""
        if not self.enabled:
            return self._local().upsert(table, data, on_conflict=on_conflict)

        headers = self.headers.copy()
        headers["Prefer"] = f"resolution=merge-duplicates, return=representation"
        
        url = f"{self.base_url}/{table}"
        if on_conflict:
            url += f"?on_conflict={on_conflict}"
            
        try:
            response = self._client.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            return result[0] if result else None
        except Exception as e:
            logger.warning(f"Supabase upsert failed on {table} ({e}); falling back to local SQLite")
            return self._local().upsert(table, data, on_conflict=on_conflict)

    async def async_upsert(self, table: str, data: Dict[str, Any], on_conflict: str = "name") -> Optional[Dict[str, Any]]:
        """Upsert a row into a table (async wrapper)."""
        import asyncio
        return await asyncio.to_thread(self.upsert, table, data, on_conflict)

    def update(self, table: str, filters: Dict[str, str], data: Dict[str, Any]) -> bool:
        """Update rows in a table (sync)."""
        if not self.enabled:
            return self._local().update(table, filters, data)

        url = f"{self.base_url}/{table}"
        first = True
        for k, v in filters.items():
            url += ("?" if first else "&") + f"{k}={v}"
            first = False

        try:
            response = self._client.patch(url, headers=self.headers, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Supabase update failed on {table} ({e}); falling back to local SQLite")
            return self._local().update(table, filters, data)

    async def async_update(self, table: str, filters: Dict[str, str], data: Dict[str, Any]) -> bool:
        """Update rows in a table (async wrapper)."""
        import asyncio
        return await asyncio.to_thread(self.update, table, filters, data)

    def delete(self, table: str, filters: Dict[str, str]) -> bool:
        """Delete rows from a table (sync)."""
        if not self.enabled:
            return self._local().delete(table, filters)

        url = f"{self.base_url}/{table}"
        first = True
        for k, v in filters.items():
            url += ("?" if first else "&") + f"{k}={v}"
            first = False

        try:
            response = self._client.delete(url, headers=self.headers)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Supabase delete failed on {table} ({e}); falling back to local SQLite")
            return self._local().delete(table, filters)

    async def async_delete(self, table: str, filters: Dict[str, str]) -> bool:
        """Delete rows from a table (async wrapper)."""
        import asyncio
        return await asyncio.to_thread(self.delete, table, filters)

    def rpc(self, function_name: str, params: Dict[str, Any]) -> Any:
        """Call a Supabase stored procedure (RPC)."""
        if not self.enabled:
            return self._local().rpc(function_name, params)

        url = f"{self.url}/rest/v1/rpc/{function_name}"
        from src.shared.benchmark import timer
        with timer(f"Supabase RPC {function_name}"):
            try:
                response = self._client.post(url, headers=self.headers, json=params)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.warning(f"Supabase RPC failed for {function_name} ({e}); falling back to local SQLite")
                return self._local().rpc(function_name, params)

    async def async_rpc(self, function_name: str, params: Dict[str, Any]) -> Any:
        """Call a Supabase stored procedure (async wrapper)."""
        import asyncio
        return await asyncio.to_thread(self.rpc, function_name, params)


def get_supabase_manager() -> SupabaseManager:
    return SupabaseManager()
