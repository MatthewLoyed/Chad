"""
Canvas Content Tools
Tools for interacting with Canvas files, pages, and announcements.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from src.tools.base_tool import BaseTool
from src.tools.canvas.manager import CanvasManager, logger

class GetCourseAnnouncementsTool(BaseTool):
    """Tool to fetch recent announcements from a Canvas course."""
    
    name = "get_course_announcements"
    description = "Fetch the last 3 announcements for a Canvas course."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "integer",
                        "description": "Canvas course ID. Use get_canvas_courses to find this."
                    }
                },
                "required": ["course_id"]
            }
        }
    
    async def execute(self, course_id: int, **kwargs) -> Dict[str, Any]:
        try:
            # Canvas announcements are a type of discussion topic
            result = self._manager.get(
                "/announcements",
                params={
                    "context_codes[]": f"course_{course_id}",
                    "per_page": 3,
                    "latest_only": True
                }
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            announcements = result["data"]
            
            if not announcements:
                return {
                    "success": True,
                    "result": "No announcements found for this course.",
                    "announcements": []
                }
            
            # Format announcements for clean output
            formatted_announcements = []
            for announcement in announcements:
                posted_at = announcement.get("posted_at")
                posted_date_str = ""
                if posted_at:
                    try:
                        posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                        posted_date_str = posted_dt.strftime("%B %d, %Y at %I:%M %p")
                    except ValueError:
                        posted_date_str = posted_at
                
                # Strip HTML from message for cleaner output
                message = announcement.get("message", "")
                message_text = re.sub(r"<[^>]+>", " ", message)
                message_text = re.sub(r"\s+", " ", message_text).strip()
                
                formatted_announcements.append({
                    "id": announcement.get("id"),
                    "title": announcement.get("title", "Untitled Announcement"),
                    "posted_at": posted_date_str or "Unknown date",
                    "author": announcement.get("author", {}).get("display_name", "Unknown"),
                    "message_preview": message_text[:300] + "..." if len(message_text) > 300 else message_text,
                    "html_url": announcement.get("html_url", "")
                })
            
            return {
                "success": True,
                "result": f"Found {len(formatted_announcements)} announcements.",
                "announcements": formatted_announcements
            }
            
        except Exception as e:
            logger.error(f"Failed to get course announcements: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetCourseFilesTool(BaseTool):
    """Tool to fetch files from a Canvas course (syllabus, lecture notes, etc.)."""
    
    name = "get_course_files"
    description = "List files in a Canvas course. Use this to find lecture notes, syllabus, or assignment attachments."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {
                        "type": "string",
                        "description": "Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project', 'Wireless Networks')."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric Canvas course ID."
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Optional search term to filter files by name (e.g., 'syllabus', 'lecture')."
                    },
                    "content_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional content types to filter (e.g., ['application/pdf', 'text/plain']). Omit for all types."
                    }
                }
            }
        }
    
    async def execute(self, course_id: Optional[Union[str, int]] = None, course: Optional[Union[str, int]] = None, search_term: str = None, content_types: List[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            cid, _, err = self._manager.resolve_course_id(course_id or course or kwargs.get("course_name"))
            if err:
                return {"success": False, "error": err}

            # Build params for API request
            params = {
                "per_page": 50,
                "sort": "updated_at",
                "order": "desc"
            }
            
            if search_term:
                params["search_term"] = search_term
            
            if content_types:
                params["content_types[]"] = content_types
            
            # Fetch files from course
            result = self._manager.get(
                f"/courses/{cid}/files",
                params=params,
                paginate=True
            )
            
            if not result["success"]:
                err = str(result.get("error", ""))
                if "403" in err or "Forbidden" in err or "permission" in err.lower():
                    logger.warning(f"Canvas API returned 403 Forbidden for course {cid} files tab.")
                    return {
                        "success": False,
                        "error_code": "CANVAS_LOCKED_FILES",
                        "error": (
                            f"CANVAS_LOCKED_FILES (HTTP 403 Forbidden): The instructor for course {cid} "
                            "has locked/disabled the raw 'Files' tab for students. Course materials (lecture slides, "
                            "notes, PDFs, handouts) are published under Modules. Use 'search_module_items' or "
                            "'get_course_modules' to find and access these files. If module tools are not currently "
                            "in your toolset, call search_for_tool(query='modules', category='canvas') to load them."
                        ),
                        "hint": "Course files tab is locked by instructor. Use search_module_items or get_course_modules to find lecture files."
                    }
                return {"success": False, "error": result["error"]}
            
            files = result["data"]
            
            if not files:
                match_suffix = f' matching "{search_term}"' if search_term else ''
                return {
                    "success": True,
                    "result": f"No files found{match_suffix}.",
                    "files": []
                }
            
            # Format files for clean output
            formatted_files = []
            for file in files[:20]:  # Limit to 20 files to avoid context bloat
                # Format file size
                size_bytes = file.get("size", 0)
                if size_bytes >= 1024 * 1024:
                    size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                elif size_bytes >= 1024:
                    size_str = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_str = f"{size_bytes} bytes"
                
                # Format updated date
                updated_at = file.get("updated_at")
                updated_str = ""
                if updated_at:
                    try:
                        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        updated_str = updated_dt.strftime("%B %d, %Y")
                    except ValueError:
                        updated_str = updated_at
                
                formatted_files.append({
                    "id": file.get("id"),
                    "name": file.get("display_name") or file.get("filename", "Unknown"),
                    "size": size_str,
                    "content_type": file.get("content-type", "unknown"),
                    "updated_at": updated_str or "Unknown date",
                    "url": file.get("url", ""),  # Direct download URL
                    "folder_path": file.get("folder_path", "")
                })
            
            match_suffix = f' matching "{search_term}"' if search_term else ''
            return {
                "success": True,
                "result": f"Found {len(files)} files{match_suffix}. Showing first {len(formatted_files)}.",
                "files": formatted_files
            }
            
        except Exception as e:
            logger.error(f"Failed to get course files: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class ReadCourseFileTool(BaseTool):
    """Tool to download and read the text content of a Canvas file."""
    
    name = "read_course_file"
    description = "Read the content of a Canvas file. Supports PDF (extracts text), Word (.docx), and plain text."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "integer",
                        "description": "Canvas file ID. Use get_course_files to find this."
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to read (default: 5000)."
                    }
                },
                "required": ["file_id"]
            }
        }
    
    def _extract_text_from_pdf(self, content: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import io
            from PyPDF2 import PdfReader
            
            pdf_file = io.BytesIO(content)
            reader = PdfReader(pdf_file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        except ImportError:
            return "Error: PyPDF2 not installed. Cannot read PDF."
        except Exception as e:
            return f"Error reading PDF: {str(e)}"

    def _extract_text_from_docx(self, content: bytes) -> str:
        """Extract text from Word document bytes."""
        try:
            import io
            import docx
            
            doc_file = io.BytesIO(content)
            doc = docx.Document(doc_file)
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        except ImportError:
            return "Error: python-docx not installed. Cannot read DOCX."
        except Exception as e:
            return f"Error reading DOCX: {str(e)}"
    
    async def execute(self, file_id: int, max_chars: int = 5000, **kwargs) -> Dict[str, Any]:
        try:
            import requests
            file_id = int(file_id)
            max_chars = int(max_chars) if max_chars is not None else 5000
            
            # Get file info to get the download URL
            file_info = self._manager.get(f"/files/{file_id}")
            
            if not file_info["success"]:
                return {"success": False, "error": file_info["error"]}
            
            file_data = file_info["data"]
            download_url = file_data.get("url")
            filename = file_data.get("filename", "unknown_file")
            content_type = file_data.get("content-type", "")
            
            if not download_url:
                return {"success": False, "error": "No download URL provided for this file."}
            
            # Download file content
            response = requests.get(download_url)
            response.raise_for_status()
            
            content_bytes = response.content
            text_content = ""
            
            # Extract text based on file type
            if "pdf" in content_type or filename.lower().endswith(".pdf"):
                text_content = self._extract_text_from_pdf(content_bytes)
            elif "word" in content_type or filename.lower().endswith(".docx"):
                text_content = self._extract_text_from_docx(content_bytes)
            else:
                # Assume text/plain or similar
                try:
                    text_content = content_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    text_content = f"[Binary file: {filename} ({content_type}) - Cannot read text content]"
            
            # Truncate if too long
            original_length = len(text_content)
            if max_chars and len(text_content) > max_chars:
                text_content = text_content[:max_chars] + f"\n\n... [Truncated {original_length - max_chars} characters] ..."
            
            return {
                "success": True,
                "result": f"Read file '{filename}' ({len(text_content)} characters)",
                "filename": filename,
                "content": text_content
            }
            
        except Exception as e:
            logger.error(f"Failed to read course file: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetCourseSyllabusTool(BaseTool):
    """Tool to fetch the syllabus content directly from a Canvas course."""
    
    name = "get_course_syllabus"
    description = "Read the syllabus body directly from the course settings."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "integer",
                        "description": "Canvas course ID. Use get_canvas_courses to find this."
                    }
                },
                "required": ["course_id"]
            }
        }
    
    def _strip_html(self, html_content: str) -> str:
        """Strip HTML tags from content and clean up whitespace."""
        if not html_content:
            return ""
        
        # Remove script and style elements entirely
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Convert common block elements to newlines
        text = re.sub(r'</?(p|div|br|li|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
        
        # Remove all remaining HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        
        # Decode entities
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        
        # Normalize whitespace
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        return text.strip()
    
    async def execute(self, course_id: int, **kwargs) -> Dict[str, Any]:
        try:
            # Fetch course details including syllabus_body
            result = self._manager.get(
                f"/courses/{course_id}",
                params={"include[]": ["syllabus_body", "term"]}
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            course_data = result["data"]
            syllabus_html = course_data.get("syllabus_body", "")
            
            if not syllabus_html:
                return {
                    "success": True,
                    "result": "Syllabus body is empty for this course. Check course files instead.",
                    "syllabus": ""
                }
            
            # Clean HTML
            syllabus_text = self._strip_html(syllabus_html)
            
            return {
                "success": True,
                "result": f"Read syllabus for '{course_data.get('name')}'",
                "syllabus": syllabus_text[:5000]  # Limit length
            }
            
        except Exception as e:
            logger.error(f"Failed to get syllabus: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetCoursePagesTool(BaseTool):
    """Tool to list wiki pages in a Canvas course."""
    
    name = "get_course_pages"
    description = "List all wiki pages in a Canvas course. Pages often contain course policies, resources, study guides, etc."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "integer",
                        "description": "Canvas course ID. Use get_canvas_courses to find this."
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Optional search term to filter pages by title."
                    }
                },
                "required": ["course_id"]
            }
        }
    
    async def execute(self, course_id: int, search_term: str = None, **kwargs) -> Dict[str, Any]:
        try:
            params = {
                "per_page": 50,
                "sort": "title",
                "order": "asc"
            }
            
            if search_term:
                params["search_term"] = search_term
            
            result = self._manager.get(
                f"/courses/{course_id}/pages",
                params=params,
                paginate=True
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            pages = result["data"]
            
            if not pages:
                match_suffix = f' matching "{search_term}"' if search_term else ''
                return {
                    "success": True,
                    "result": f"No pages found{match_suffix}.",
                    "pages": []
                }
            
            formatted_pages = []
            for page in pages:
                formatted_pages.append({
                    "page_id": page.get("page_id"),
                    "url": page.get("url"),  # Used for fetching content
                    "title": page.get("title", "Untitled Page"),
                    "created_at": page.get("created_at", ""),
                    "updated_at": page.get("updated_at", ""),
                    "published": page.get("published", True)
                })
            
            return {
                "success": True,
                "result": f"Found {len(formatted_pages)} pages.",
                "pages": formatted_pages
            }
            
        except Exception as e:
            logger.error(f"Failed to get course pages: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class ReadCoursePageTool(BaseTool):
    """Tool to read the content of a Canvas wiki page."""
    
    name = "read_course_page"
    description = "Read the full content of a Canvas wiki page."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "integer",
                        "description": "Canvas course ID."
                    },
                    "page_url": {
                        "type": "string",
                        "description": "The page URL identifier (e.g. 'home-page') from get_course_pages."
                    }
                },
                "required": ["course_id", "page_url"]
            }
        }
    
    def _strip_html(self, html_content: str) -> str:
        """Strip HTML tags from content."""
        if not html_content:
            return ""
        
        # Remove script and style elements entirely
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Convert common block elements to newlines
        text = re.sub(r'</?(p|div|br|li|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
        
        # Remove all remaining HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        
        # Decode entities
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        
        # Normalize whitespace
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        return text.strip()
    
    async def execute(self, course_id: int, page_url: str, **kwargs) -> Dict[str, Any]:
        try:
            result = self._manager.get(f"/courses/{course_id}/pages/{page_url}")
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            page_data = result["data"]
            body = page_data.get("body", "")
            title = page_data.get("title", "Untitled")
            
            content = self._strip_html(body)
            
            return {
                "success": True,
                "result": f"Read page '{title}' ({len(content)} characters)",
                "title": title,
                "content": content
            }
            
        except Exception as e:
            logger.error(f"Failed to read course page: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class DownloadCourseFileTool(BaseTool):
    """Tool to download a Canvas file to a local path."""
    
    name = "download_course_file"
    description = "Download a Canvas file directly to your local computer."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "integer",
                        "description": "Canvas file ID."
                    },
                    "destination_path": {
                        "type": "string",
                        "description": "Optional: Full local path where to save the file. Defaults to your Downloads folder."
                    }
                },
                "required": ["file_id"]
            }
        }

    async def execute(self, file_id: int, destination_path: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            import requests
            import os
            from src.shared.config_manager import ConfigManager
            file_id = int(file_id)
            
            # Get file info
            file_info = self._manager.get(f"/files/{file_id}")
            if not file_info["success"]:
                return {"success": False, "error": file_info["error"]}
            
            file_data = file_info["data"]
            download_url = file_data.get("url")
            filename = file_data.get("filename", "unknown_file")
            
            if not download_url:
                return {"success": False, "error": "No download URL provided by Canvas."}
            
            # Determine destination
            if not destination_path:
                # Get the profile config to find default download dir
                profile_cfg = ConfigManager.load_config().get("profiles", {}).get(ConfigManager.get_active_profile(), {})
                download_dir = profile_cfg.get("file_writer", {}).get("default_directory", "~/Downloads")
                destination_path = os.path.join(ConfigManager.expand_path(download_dir), filename)
            else:
                destination_path = ConfigManager.expand_path(destination_path)
            
            dest = Path(destination_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            
            # Download
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            with open(dest, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            return {
                "success": True,
                "result": f"Successfully downloaded '{filename}' to {dest}",
                "path": str(dest),
                "filename": filename
            }
            
        except Exception as e:
            logger.error(f"Failed to download course file: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
