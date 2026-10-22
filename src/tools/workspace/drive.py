"""
Google Workspace Drive & Docs Tools

Native replacements for the most-used Google Drive MCP tools. Niche MCP tools
(comments, formatting, sheets, etc.) remain available via the google-drive server.
"""

import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from googleapiclient.errors import HttpError

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.tools.workspace.manager import GoogleWorkspaceManager
from src.tools.display_helpers import (
    format_drive_search,
    format_drive_list_folder,
    format_drive_read,
    format_drive_create_doc,
    format_drive_edit_doc,
)

logger = setup_logger("GoogleWorkspace")

MIME_FOLDERS = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_SLIDE = "application/vnd.google-apps.presentation"

FILE_TYPE_MIMES = {
    "folder": MIME_FOLDERS,
    "document": MIME_DOC,
    "doc": MIME_DOC,
    "spreadsheet": MIME_SHEET,
    "sheet": MIME_SHEET,
    "presentation": MIME_SLIDE,
    "slides": MIME_SLIDE,
    "pdf": "application/pdf",
}


def resolve_drive_file_id(file_id_or_url: str) -> str:
    """Accept a raw Drive file ID or a Docs/Drive share URL; return the file ID."""
    s = (file_id_or_url or "").strip()
    if not s:
        return s
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    return s


def looks_like_drive_file_id(file_id: str) -> bool:
    """True for Drive ids / share URLs — false for document titles used by mistake."""
    s = (file_id or "").strip()
    if not s:
        return False
    if "/d/" in s or "id=" in s:
        return True
    if " " in s:
        return False
    # Typical Drive ids are long alphanumeric; titles often have underscores/short tokens.
    if len(s) < 25:
        return False
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]+", s))


def _escape_drive_query(value: str) -> str:
    """Escape single quotes for Google Drive query strings."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _auth_error_response(error: PermissionError) -> Dict[str, Any]:
    return {
        "success": False,
        "error": (
            f"Authentication required: {error}. "
            "Please call the 'refresh_google_auth' tool to re-authenticate with Google."
        ),
    }


def _format_drive_item(file_meta: Dict[str, Any]) -> Dict[str, Any]:
    mime = file_meta.get("mimeType", "")
    item_type = "folder" if mime == MIME_FOLDERS else "file"
    return {
        "id": file_meta.get("id"),
        "name": file_meta.get("name"),
        "type": item_type,
        "mime_type": mime,
        "modified": file_meta.get("modifiedTime", ""),
        "link": file_meta.get("webViewLink", ""),
    }


def _extract_doc_text(doc: Dict[str, Any]) -> str:
    parts: List[str] = []
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" not in element:
            continue
        for para_element in element["paragraph"].get("elements", []):
            if "textRun" in para_element:
                parts.append(para_element["textRun"].get("content", ""))
    return "".join(parts)


class GoogleDriveListFolderTool(BaseTool):
    """List files and folders inside a Google Drive folder."""

    name = "google_drive_list_folder"
    description = (
        "LIST: Browse a Google Drive folder and return its files and subfolders. "
        "Use for 'list my drive folder', 'what's in this folder', or after search to explore contents."
    )
    category = "workspace"
    is_side_effecting = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def format_action(self, params: Dict[str, Any]) -> str:
        return format_drive_list_folder(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "Google Drive folder ID. Omit or use 'root' for My Drive root.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum items to return (default: 50).",
                    },
                },
                "required": [],
            },
        }

    async def execute(
        self,
        folder_id: Optional[str] = None,
        limit: int = 50,
        **kwargs,
    ) -> Dict[str, Any]:
        folder_id = folder_id or "root"
        try:
            limit = int(limit) if limit is not None else 50
        except (ValueError, TypeError):
            limit = 50
        limit = max(1, min(limit, 100))
        try:
            drive = self._manager.get_drive_service()
            if not drive:
                return {"success": False, "error": "Failed to connect to Google Drive."}

            query = f"'{_escape_drive_query(folder_id)}' in parents and trashed = false"
            results = drive.files().list(
                q=query,
                pageSize=limit,
                fields="files(id, name, mimeType, modifiedTime, webViewLink)",
                orderBy="folder,name",
            ).execute()

            items = [_format_drive_item(f) for f in results.get("files", [])]
            folder_label = "My Drive root" if folder_id == "root" else folder_id

            return {
                "success": True,
                "result": f"Found {len(items)} items in folder '{folder_label}'",
                "folder_id": folder_id,
                "items": items,
            }
        except PermissionError as e:
            return _auth_error_response(e)
        except HttpError as e:
            logger.error(f"Drive list folder error: {e}", exc_info=True)
            return {"success": False, "error": f"Drive API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to list Drive folder: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


_DRIVE_SEARCH_STOP_WORDS = {
    "the", "a", "an", "on", "in", "for", "and", "or", "to", "from", "of", "with",
    "by", "at", "about", "is", "it", "that", "this", "my", "our", "your",
    "google", "drive", "spreadsheet", "spreadsheets", "sheet", "sheets",
    "doc", "docs", "document", "documents", "file", "files", "folder", "folders",
}


def _extract_drive_search_tokens(query: str) -> List[str]:
    raw_tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9]+", query or "") if len(t) > 1]
    meaningful = [t for t in raw_tokens if t not in _DRIVE_SEARCH_STOP_WORDS and len(t) > 2]
    return meaningful if meaningful else [t for t in raw_tokens if len(t) > 2]


def _score_drive_file(file_meta: Dict[str, Any], query: str, tokens: List[str]) -> float:
    name = (file_meta.get("name") or "").lower()
    score = 0.0

    # Exact phrase in name
    clean_query = query.strip().lower()
    if clean_query and clean_query in name:
        score += 15.0

    # Token matches in name
    matched_tokens = 0
    for t in tokens:
        if t in name:
            matched_tokens += 1
            if re.search(rf"\b{re.escape(t)}\b", name):
                score += 4.0
            else:
                score += 2.0

    if tokens:
        score += (matched_tokens / len(tokens)) * 5.0

    # Recency bonus
    mod_str = file_meta.get("modifiedTime")
    if mod_str:
        try:
            mod_dt = datetime.fromisoformat(mod_str.replace("Z", "+00:00"))
            days_ago = (datetime.now(timezone.utc) - mod_dt).days
            if days_ago < 7:
                score += 3.0
            elif days_ago < 30:
                score += 1.5
            elif days_ago < 90:
                score += 0.5
        except Exception:
            pass

    return score


class GoogleDriveSearchTool(BaseTool):
    """Search Google Drive for files and folders by name or content."""

    name = "google_drive_search"
    description = (
        "SEARCH: Find files or folders in Google Drive by name or keywords. "
        "Returns IDs and links you can pass to google_drive_read_file or google_drive_list_folder."
    )
    category = "workspace"
    is_side_effecting = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def format_action(self, params: Dict[str, Any]) -> str:
        return format_drive_search(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search text, e.g. 'wedding photos', 'budget spreadsheet', 'homework'.",
                    },
                    "file_type": {
                        "type": "string",
                        "description": (
                            "Optional filter: all (default), folder, document, spreadsheet, "
                            "presentation, or pdf."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 20).",
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(
        self,
        query: str,
        file_type: str = "all",
        limit: int = 20,
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            drive = self._manager.get_drive_service()
            if not drive:
                return {"success": False, "error": "Failed to connect to Google Drive."}

            tokens = _extract_drive_search_tokens(query)
            safe_query = _escape_drive_query(query.strip())

            try:
                limit = int(limit) if limit is not None else 20
            except (ValueError, TypeError):
                limit = 20
            limit = max(1, min(limit, 100))

            file_type = file_type or "all"
            mime = FILE_TYPE_MIMES.get(file_type.lower())
            mime_clause = f" and mimeType = '{mime}'" if mime else ""

            queries: List[str] = []
            # 1. Tokenized query: search for individual semantic tokens in file names
            if tokens:
                token_clauses = " or ".join([f"name contains '{_escape_drive_query(t)}'" for t in tokens])
                queries.append(f"trashed = false and ({token_clauses}){mime_clause}")

            # 2. Exact phrase query (name or fullText)
            if safe_query:
                queries.append(f"trashed = false and (name contains '{safe_query}' or fullText contains '{safe_query}'){mime_clause}")

            seen_ids = set()
            found_raw_files: List[Dict[str, Any]] = []

            for q_str in queries:
                try:
                    results = drive.files().list(
                        q=q_str,
                        pageSize=max(limit * 2, 40),
                        fields="files(id, name, mimeType, modifiedTime, webViewLink)",
                        orderBy="modifiedTime desc",
                    ).execute()
                    for f in results.get("files", []):
                        fid = f.get("id")
                        if fid and fid not in seen_ids:
                            seen_ids.add(fid)
                            found_raw_files.append(f)
                except Exception as query_err:
                    logger.debug(f"Sub-query '{q_str}' failed: {query_err}")

            # Score and rank results by relevance
            scored = [(_score_drive_file(f, query, tokens), f) for f in found_raw_files]
            scored.sort(key=lambda x: x[0], reverse=True)

            files = [_format_drive_item(f) for _, f in scored[:limit]]
            return {
                "success": True,
                "result": f"Found {len(files)} items matching '{query}'",
                "query": query,
                "files": files,
            }
        except PermissionError as e:
            return _auth_error_response(e)
        except HttpError as e:
            logger.error(f"Drive search error: {e}", exc_info=True)
            return {"success": False, "error": f"Drive API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to search Drive: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GoogleDriveReadFileTool(BaseTool):
    """Read the text content of a Google Drive file."""

    name = "google_drive_read_file"
    description = (
        "READ: Get the text content of a Google Drive file by file ID or share URL. "
        "Works with Google Docs, Sheets (CSV export), Slides (plain text), and plain text. "
        "Use the id= from DOCUMENT CONTEXT (DRIVE) catalog hits when you need the body."
    )
    category = "workspace"
    is_side_effecting = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def _lookup_file_name(self, file_id: Optional[str]) -> Optional[str]:
        if not file_id:
            return None
        try:
            drive = self._manager.get_drive_service()
            if not drive:
                return None
            meta = drive.files().get(fileId=file_id, fields="name").execute()
            return meta.get("name")
        except Exception:
            return None

    def format_action(self, params: Dict[str, Any]) -> str:
        file_id = resolve_drive_file_id(params.get("file_id") or "")
        return format_drive_read(params, self._lookup_file_name(file_id or None))

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": (
                            "Google Drive file ID, or a Docs/Drive share URL "
                            "(…/d/<id>/… or ?id=<id>). From search, list, or DOCUMENT CONTEXT id=."
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Optional cap on returned characters (default: 50000).",
                    },
                },
                "required": ["file_id"],
            },
        }

    async def execute(
        self,
        file_id: str,
        max_chars: int = 50000,
        **kwargs,
    ) -> Dict[str, Any]:
        raw_id = (file_id or "").strip()
        if not looks_like_drive_file_id(raw_id):
            return {
                "success": False,
                "error": (
                    f"'{raw_id}' does not look like a Google Drive file_id. "
                    "Pass the `id` from google_drive_search results — not the document title."
                ),
            }
        file_id = resolve_drive_file_id(raw_id)

        from src.core.orchestration.read_memory import cache_get, cache_put

        cached = cache_get(f"google_drive_read_file:{file_id}")
        if isinstance(cached, dict) and cached.get("success"):
            n = len(cached.get("content") or "")
            return {
                "success": True,
                "result": (
                    f"Content already retrieved earlier this gather "
                    f"({n} chars for file_id={file_id}). Use that prior result; do not re-fetch."
                ),
                "file_id": file_id,
                "name": cached.get("name"),
                "content": cached.get("content") or "",
                "cached": True,
            }

        try:
            drive = self._manager.get_drive_service()
            if not drive:
                return {"success": False, "error": "Failed to connect to Google Drive."}

            meta = drive.files().get(
                fileId=file_id,
                fields="id, name, mimeType, webViewLink",
            ).execute()
            mime = meta.get("mimeType", "")
            name = meta.get("name", "Untitled")

            if mime == MIME_FOLDERS:
                return {
                    "success": False,
                    "error": f"'{name}' is a folder. Use google_drive_list_folder with this ID instead.",
                }

            content = ""
            if mime == MIME_DOC:
                docs = self._manager.get_docs_service()
                if not docs:
                    return {"success": False, "error": "Failed to connect to Google Docs."}
                doc = docs.documents().get(documentId=file_id).execute()
                content = _extract_doc_text(doc)
            elif mime == MIME_SHEET:
                raw = drive.files().export(fileId=file_id, mimeType="text/csv").execute()
                content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            elif mime == MIME_SLIDE:
                raw = drive.files().export(fileId=file_id, mimeType="text/plain").execute()
                content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            else:
                try:
                    raw = drive.files().get_media(fileId=file_id).execute()
                    content = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
                except Exception:
                    return {
                        "success": True,
                        "result": f"File '{name}' is binary or unsupported for inline text.",
                        "file_id": file_id,
                        "name": name,
                        "mime_type": mime,
                        "link": meta.get("webViewLink", ""),
                        "content": "",
                    }

            truncated = False
            if max_chars and len(content) > max_chars:
                content = content[:max_chars]
                truncated = True

            out = {
                "success": True,
                "result": f"Read file '{name}' ({len(content)} chars)",
                "file_id": file_id,
                "name": name,
                "mime_type": mime,
                "link": meta.get("webViewLink", ""),
                "content": content,
                "truncated": truncated,
            }
            cache_put(f"google_drive_read_file:{file_id}", out)
            return out
        except PermissionError as e:
            return _auth_error_response(e)
        except HttpError as e:
            if e.resp.status == 404:
                return {"success": False, "error": f"File not found: {file_id}"}
            logger.error(f"Drive read error: {e}", exc_info=True)
            return {"success": False, "error": f"Drive API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to read Drive file: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GoogleDriveCreateDocTool(BaseTool):
    """Create a new Google Doc with optional initial content."""

    name = "google_drive_create_doc"
    description = (
        "CREATE: Make a new Google Doc with a title and optional body text. "
        "For an exact copy, pass content_id from browser_capture_text instead of retyping content; "
        "the stored text is inserted directly and verified by reading the document back. "
        "Returns the new document ID and edit link."
    )
    category = "workspace"
    is_side_effecting = True

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def format_action(self, params: Dict[str, Any]) -> str:
        return format_drive_create_doc(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title for the new Google Doc.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Optional initial text content.",
                    },
                    "folder_id": {
                        "type": "string",
                        "description": "Optional parent folder ID to create the doc inside.",
                    },
                    "content_id": {
                        "type": "string",
                        "description": "Captured text ID from browser_capture_text in this conversation. Use instead of content for a verbatim copy. Preserves text and paragraph breaks, not rich formatting.",
                    },
                },
                "required": ["title"],
            },
        }

    async def execute(
        self,
        title: str,
        content: str = "",
        folder_id: Optional[str] = None,
        content_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        doc_id = None
        captured = None
        try:
            if content_id:
                if content:
                    return {"success": False, "error": "Supply content_id or content, not both."}
                from src.shared.captured_content import load_captured_text
                captured = load_captured_text(content_id)
                content = captured['text']
            docs = self._manager.get_docs_service()
            if not docs:
                return {"success": False, "error": "Failed to connect to Google Docs."}

            doc = docs.documents().create(body={"title": title}).execute()
            doc_id = doc.get("documentId")
            if not doc_id:
                return {"success": False, "error": "Google Docs did not return a document ID."}

            if content:
                docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={
                        "requests": [
                            {"insertText": {"location": {"index": 1}, "text": content}}
                        ]
                    },
                ).execute()

            if captured:
                # A new Doc has one mandatory terminal paragraph newline. Compare
                # every captured character, allowing ONLY that structural terminator.
                actual = _extract_doc_text(docs.documents().get(documentId=doc_id).execute())
                if actual != content + '\n':
                    return {
                        "success": False, "verified": False, "doc_id": doc_id,
                        "link": f"https://docs.google.com/document/d/{doc_id}/edit",
                        "content_id": content_id,
                        "error": "Document created but exact-text verification failed. Do not claim a verbatim copy or create a duplicate; inspect this document.",
                    }

            if folder_id and folder_id != "root":
                drive = self._manager.get_drive_service()
                if drive:
                    drive.files().update(
                        fileId=doc_id,
                        addParents=folder_id,
                        fields="id, parents",
                    ).execute()

            link = f"https://docs.google.com/document/d/{doc_id}/edit"
            logger.info(f"Created Google Doc: {doc_id}")

            return {
                "success": True,
                "result": f"Created document '{title}'",
                "doc_id": doc_id,
                "link": link,
                **({"verified": True, "content_id": content_id,
                    "char_count": captured['char_count'], "sha256": captured['sha256']} if captured else {}),
            }
        except PermissionError as e:
            response = _auth_error_response(e)
            if doc_id:
                response.update(doc_id=doc_id, verified=False,
                                link=f"https://docs.google.com/document/d/{doc_id}/edit")
            return response
        except HttpError as e:
            logger.error(f"Create doc error: {e}", exc_info=True)
            return {"success": False, "error": f"Docs API error: {e}",
                    **({"doc_id": doc_id, "link": f"https://docs.google.com/document/d/{doc_id}/edit",
                        "verified": False} if doc_id else {})}
        except Exception as e:
            logger.error(f"Failed to create Google Doc: {e}", exc_info=True)
            return {"success": False, "error": str(e),
                    **({"doc_id": doc_id, "link": f"https://docs.google.com/document/d/{doc_id}/edit",
                        "verified": False} if doc_id else {})}


class GoogleDriveEditDocTool(BaseTool):
    """Edit an existing Google Doc by appending text or find-and-replace."""

    name = "google_drive_edit_doc"
    description = (
        "EDIT: Modify an existing Google Doc. Append text to the end, or find and replace text. "
        "Provide doc_id from google_drive_search or google_drive_read_file."
    )
    category = "workspace"
    is_side_effecting = True

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def _lookup_doc_name(self, doc_id: Optional[str]) -> Optional[str]:
        if not doc_id:
            return None
        try:
            drive = self._manager.get_drive_service()
            if not drive:
                return None
            meta = drive.files().get(fileId=doc_id, fields="name").execute()
            return meta.get("name")
        except Exception:
            return None

    def format_action(self, params: Dict[str, Any]) -> str:
        doc_id = params.get("doc_id")
        return format_drive_edit_doc(params, self._lookup_doc_name(doc_id))

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {
                        "type": "string",
                        "description": "Google Doc file ID to edit.",
                    },
                    "append_text": {
                        "type": "string",
                        "description": "Text to append at the end of the document.",
                    },
                    "find_text": {
                        "type": "string",
                        "description": "Text to find for replacement (use with replace_text).",
                    },
                    "replace_text": {
                        "type": "string",
                        "description": "Replacement text for each match of find_text.",
                    },
                    "replace_all_content": {
                        "type": "string",
                        "description": (
                            "Replace the entire document body with this text. "
                            "Mutually exclusive with append/find-replace."
                        ),
                    },
                },
                "required": ["doc_id"],
            },
        }

    async def execute(
        self,
        doc_id: str,
        append_text: Optional[str] = None,
        find_text: Optional[str] = None,
        replace_text: Optional[str] = None,
        replace_all_content: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        has_append = bool(append_text)
        has_replace = bool(find_text) or bool(replace_text)
        has_full_replace = bool(replace_all_content)

        if sum([has_append, has_replace, has_full_replace]) != 1:
            return {
                "success": False,
                "error": (
                    "Provide exactly one edit mode: append_text, "
                    "find_text+replace_text, or replace_all_content."
                ),
            }
        if has_replace and (not find_text or replace_text is None):
            return {
                "success": False,
                "error": "Both find_text and replace_text are required for find-and-replace.",
            }

        try:
            docs = self._manager.get_docs_service()
            if not docs:
                return {"success": False, "error": "Failed to connect to Google Docs."}

            requests: List[Dict[str, Any]] = []

            if has_append:
                requests.append(
                    {
                        "insertText": {
                            "endOfSegmentLocation": {"segmentId": ""},
                            "text": append_text,
                        }
                    }
                )
            elif has_replace:
                requests.append(
                    {
                        "replaceAllText": {
                            "containsText": {"text": find_text, "matchCase": False},
                            "replaceText": replace_text,
                        }
                    }
                )
            else:
                doc = docs.documents().get(documentId=doc_id).execute()
                end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)
                if end_index > 2:
                    requests.append(
                        {
                            "deleteContentRange": {
                                "range": {
                                    "startIndex": 1,
                                    "endIndex": end_index - 1,
                                }
                            }
                        }
                    )
                if replace_all_content:
                    requests.append(
                        {
                            "insertText": {
                                "location": {"index": 1},
                                "text": replace_all_content,
                            }
                        }
                    )

            docs.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": requests},
            ).execute()

            link = f"https://docs.google.com/document/d/{doc_id}/edit"
            mode = "appended to" if has_append else "updated"
            return {
                "success": True,
                "result": f"Successfully {mode} document {doc_id}",
                "doc_id": doc_id,
                "link": link,
            }
        except PermissionError as e:
            return _auth_error_response(e)
        except HttpError as e:
            logger.error(f"Edit doc error: {e}", exc_info=True)
            return {"success": False, "error": f"Docs API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to edit Google Doc: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
