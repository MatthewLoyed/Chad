"""
Chrome Browser History Ingestor
Extracts visited URLs, titles, and timestamps from the local Chrome SQLite DB on Windows.
Supports YouTube detection and automatic deduplication.
"""

import os
import sys
import shutil
import sqlite3
import logging
import tempfile
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional

from src.core.intelligence.ingestion.base import BaseIngestor

logger = logging.getLogger("ChromeIngestor")

# The 1601 Epoch used by Microsoft / Chromium databases
WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


class ChromeHistoryIngestor(BaseIngestor):
    """
    Safely ingests user browser history from the local Windows Chrome SQLite database.
    """

    def __init__(self, custom_history_path: Optional[str] = None):
        """
        Initialize the ingestor. Resolves default Chrome history location if none provided.
        """
        self.history_path = custom_history_path or self._get_default_path()
        logger.info(f"[ ChromeIngestor ] Resolved history path: {self.history_path}")

    def get_source_name(self) -> str:
        return "chrome"

    def _get_default_path(self) -> str:
        """Resolves the standard Chrome Default profile path on Windows."""
        if sys.platform != "win32":
            logger.warning("[ ChromeIngestor ] Non-Windows OS detected. Native Chrome path may fail.")
            
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            # Fallback attempt
            local_app_data = os.path.expandvars(r"%USERPROFILE%\AppData\Local")
            
        return os.path.join(
            local_app_data, 
            "Google", "Chrome", "User Data", "Default", "History"
        )

    def _to_chrome_timestamp(self, dt: datetime) -> int:
        """Converts a Python datetime into microseconds since Windows Epoch (1601)."""
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - WINDOWS_EPOCH
        return int(delta.total_seconds() * 1_000_000)

    def _from_chrome_timestamp(self, microseconds: int) -> datetime:
        """Converts a Chrome microsecond timestamp to Python UTC datetime."""
        return WINDOWS_EPOCH + timedelta(microseconds=microseconds)

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Queries the local Chrome database for history since last sync.
        Copies the file locally first to prevent 'database locked' errors when Chrome is running.
        """
        if not os.path.exists(self.history_path):
            logger.error(f"[ ChromeIngestor ] Chrome history file not found at: {self.history_path}")
            return []

        normalized_items = []
        temp_db_path = None

        try:
            # 1. Create a safe temporary copy to bypass browser locking
            temp_dir = tempfile.gettempdir()
            temp_db_name = f"chrome_history_sync_{int(datetime.now().timestamp())}.sqlite"
            temp_db_path = os.path.join(temp_dir, temp_db_name)
            
            shutil.copy2(self.history_path, temp_db_path)
            logger.debug(f"[ ChromeIngestor ] Copied history to temp file: {temp_db_path}")

            # 2. Connect and Query
            conn = sqlite3.connect(temp_db_path)
            cursor = conn.cursor()

            # Prepare query based on time limit
            if last_sync:
                chrome_limit = self._to_chrome_timestamp(last_sync)
                query = """
                    SELECT url, title, last_visit_time, visit_count 
                    FROM urls 
                    WHERE last_visit_time > ? 
                      AND url NOT LIKE 'chrome://%' 
                      AND url NOT LIKE 'chrome-extension://%'
                      AND url NOT LIKE 'file://%' 
                      AND url NOT LIKE 'about:%'
                    ORDER BY last_visit_time ASC
                    LIMIT 500
                """
                cursor.execute(query, (chrome_limit,))
            else:
                # If no sync time, pull last 24 hours to avoid token explosion
                one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
                chrome_limit = self._to_chrome_timestamp(one_day_ago)
                query = """
                    SELECT url, title, last_visit_time, visit_count 
                    FROM urls 
                    WHERE last_visit_time > ? 
                      AND url NOT LIKE 'chrome://%' 
                      AND url NOT LIKE 'chrome-extension://%'
                      AND url NOT LIKE 'file://%' 
                      AND url NOT LIKE 'about:%'
                    ORDER BY last_visit_time ASC
                    LIMIT 200
                """
                cursor.execute(query, (chrome_limit,))

            rows = cursor.fetchall()
            conn.close()
            
            logger.info(f"[ ChromeIngestor ] Retrieved {len(rows)} recent history rows.")

            # 3. Normalize and Structure Content
            for row in rows:
                url, title, last_visit_time, visit_count = row
                
                # Skip empty URLs or system/internal protocols
                if not url or url.startswith(('chrome://', 'file://', 'about:', 'data:')):
                    continue

                parsed_url = urlparse(url)
                domain = parsed_url.netloc.lower()
                
                # Standardize Title
                display_title = title.strip() if (title and title.strip()) else domain
                visit_time = self._from_chrome_timestamp(last_visit_time)
                
                # Specialized Formatting (YouTube, Github, Docs, Search etc.)
                item_type = "website"
                importance = 0.3 # Default low importance for raw history
                
                is_youtube = ("youtube.com" in domain or "youtu.be" in domain)
                is_youtube_video = is_youtube and (
                    "watch" in parsed_url.path or 
                    "shorts" in parsed_url.path or 
                    "youtu.be" in domain
                )
                
                if is_youtube_video:
                    item_type = "youtube_video"
                    importance = 0.4  # Slightly higher for videos
                    clean_title = display_title.replace(" - YouTube", "")
                    content_string = f"Watched YouTube Video: '{clean_title}'"
                elif "github.com" in domain:
                    item_type = "code_repo"
                    importance = 0.5
                    content_string = f"Visited GitHub: '{display_title}'"
                elif "google.com" in domain and "/search" in parsed_url.path:
                    # Try to extract query
                    item_type = "search_query"
                    content_string = f"Searched Google for: '{display_title.replace(' - Google Search', '')}'"
                elif "drive.google.com/picker/" in url or "minpick/main" in url:
                    from src.core.intelligence.knowledge_context_format import extract_picker_filename
                    picked = extract_picker_filename(url)
                    item_type = "drive_picker"
                    if picked:
                        content_string = f"Opened Drive file: '{picked}'"
                    elif display_title not in ("My Drive - Google Drive", "Google Drive"):
                        content_string = f"Opened Drive: '{display_title}'"
                    else:
                        continue
                else:
                    content_string = f"Visited Web Page: '{display_title}'"

                # Deduplication payload (URL + exact microsecond timestamp makes a globally unique visit hash)
                dedup_key = f"chrome_{url}_{last_visit_time}"
                item_hash = self.compute_hash(dedup_key)

                normalized_items.append({
                    "content": content_string,
                    "importance": importance,
                    "metadata": {
                        "source": "chrome",
                        "type": item_type,
                        "url": url,
                        "title": display_title,
                        "domain": domain,
                        "timestamp": visit_time.isoformat(),
                        "visit_count": visit_count,
                        "hash": item_hash
                    }
                })

        except sqlite3.OperationalError as db_err:
            logger.error(f"[ ChromeIngestor ] SQLite query failed. Browser might possess an exclusive lock: {db_err}")
        except Exception as e:
            logger.error(f"[ ChromeIngestor ] Ingestion cycle aborted due to error: {e}", exc_info=True)
        finally:
            # Clean up temp DB safely
            if temp_db_path and os.path.exists(temp_db_path):
                try:
                    os.remove(temp_db_path)
                except Exception as clean_err:
                    logger.warning(f"[ ChromeIngestor ] Failed to remove temp database at {temp_db_path}: {clean_err}")

        return normalized_items
