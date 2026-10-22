"""
Canvas LMS API Manager
Handles authentication, requests, and pagination for Canvas tools.
"""

from typing import Dict, Any, Optional, List, Tuple
from src.shared.logger import setup_logger

logger = setup_logger("Canvas")

DEFAULT_CONTACT_EMAIL = "user@example.com"
USER_AGENT_PREFIX = "ChadAssistant/1.0"


def normalize_canvas_api_base_url(raw: str) -> Optional[str]:
    """
    Normalize a Canvas instance URL to the REST API base (…/api/v1).

    Accepts:
      - https://school.instructure.com
      - https://school.instructure.com/
      - https://school.instructure.com/api/v1
    """
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip().rstrip("/")
    if url.endswith("/api/v1"):
        return url
    return f"{url}/api/v1"


def build_canvas_user_agent(contact_email: Optional[str] = None) -> str:
    """Canvas API compliance: include a contact address in User-Agent."""
    email = (contact_email or "").strip() or DEFAULT_CONTACT_EMAIL
    return f"{USER_AGENT_PREFIX} ({email})"


def load_canvas_config() -> Tuple[Optional[str], Optional[str], str]:
    """
    Load Canvas credentials from secrets.

    Returns:
        (api_base_url, token, user_agent)
    """
    from src.shared.config_manager import ConfigManager

    token = ConfigManager.get_secret("CANVAS_TOKEN") or ConfigManager.get_secret("canvas.token")
    base_raw = ConfigManager.get_secret("CANVAS_BASE_URL") or ConfigManager.get_secret("canvas.base_url")
    contact = (
        ConfigManager.get_secret("CANVAS_CONTACT_EMAIL")
        or ConfigManager.get_secret("canvas.contact_email")
        or DEFAULT_CONTACT_EMAIL
    )
    api_base = normalize_canvas_api_base_url(str(base_raw or ""))
    token_str = str(token).strip() if token else None
    return api_base, token_str or None, build_canvas_user_agent(str(contact))


class CanvasManager:
    """
    Singleton manager for Canvas LMS API access.
    Handles authentication, requests, and pagination.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._api_base_url: Optional[str] = None
        self._token: Optional[str] = None
        self._session_headers: Dict[str, str] = {}
        self._initialized = True

    @classmethod
    def reset_instance(cls) -> None:
        """Clear cached credentials after connector save/disconnect."""
        cls._instance = None

    def _ensure_authenticated(self) -> bool:
        """
        Load Canvas token and instance URL from secrets.

        Returns:
            True if credentials are available, False otherwise.
        """
        api_base, token, user_agent = load_canvas_config()

        if not token:
            logger.error("CANVAS_TOKEN not found in secrets")
            return False
        if not api_base:
            logger.error("CANVAS_BASE_URL not found in secrets (e.g. https://your-school.instructure.com)")
            return False

        if self._token == token and self._api_base_url == api_base and self._session_headers:
            return True

        self._token = token
        self._api_base_url = api_base
        self._session_headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": user_agent,
            "Content-Type": "application/json",
        }

        logger.info(f"Canvas API configured for {api_base}")
        return True

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None, paginate: bool = False) -> Dict[str, Any]:
        """
        Make a GET request to the Canvas API.

        Args:
            endpoint: API endpoint (e.g., '/courses')
            params: Optional query parameters
            paginate: If True, automatically fetch all pages

        Returns:
            Dictionary with 'success', 'data', and optionally 'error'
        """
        import requests

        if not self._ensure_authenticated():
            return {
                "success": False,
                "error": (
                    "Canvas is not configured yet. Ask the user for their Canvas Base URL and Access Token, "
                    "or call manage_connectors(action='get_schema', connector_id='canvas') to fetch field details, "
                    "then call manage_connectors(action='configure', connector_id='canvas', inputs={...}) to connect it directly."
                ),
            }

        url = f"{self._api_base_url}{endpoint}"
        params = params or {}

        if "per_page" not in params:
            params["per_page"] = 50

        try:
            all_data = []

            while url:
                response = requests.get(
                    url,
                    headers=self._session_headers,
                    params=params if url.startswith(self._api_base_url) else None,
                    timeout=30,
                )

                if response.status_code == 401:
                    logger.error(f"Canvas API: Unauthorized (401) - {endpoint}")
                    err_msg = ""
                    try:
                        err_json = response.json()
                        if isinstance(err_json, dict) and "errors" in err_json:
                            err_msg = "; ".join([
                                e.get("message", str(e)) if isinstance(e, dict) else str(e)
                                for e in err_json["errors"]
                            ])
                    except Exception:
                        pass
                    
                    if "invalid" in err_msg.lower() or "token" in err_msg.lower() or endpoint in ("/users/self", "/users/self/profile"):
                        return {
                            "success": False,
                            "error": (
                                "Unauthorized: Your Canvas token may be expired or invalid. "
                                "Generate a new one at Canvas → Account → Settings → "
                                "Approved Integrations → New Access Token."
                            ),
                        }
                    return {
                        "success": False,
                        "error": f"Unauthorized access for '{endpoint}': {err_msg or 'You do not have permission to access this course/item or the ID is invalid. Use get_canvas_courses to find valid course IDs.'}",
                    }

                if response.status_code == 404:
                    logger.error(f"Canvas API: Not found (404) - {endpoint}")
                    return {
                        "success": False,
                        "error": f"Not found: The requested resource '{endpoint}' does not exist or you don't have access.",
                    }

                if response.status_code == 403:
                    logger.error(f"Canvas API: Forbidden (403) - {endpoint}")
                    return {
                        "success": False,
                        "error": f"Forbidden: You don't have permission to access '{endpoint}'.",
                    }

                response.raise_for_status()

                data = response.json()

                if isinstance(data, list):
                    all_data.extend(data)
                else:
                    return {"success": True, "data": data}

                if not paginate:
                    break

                url = self._get_next_page_url(response.headers.get("Link", ""))
                params = {}

            return {"success": True, "data": all_data}

        except requests.exceptions.Timeout:
            logger.error("Canvas API request timed out")
            return {"success": False, "error": "Request timed out. Canvas may be slow or unavailable."}
        except requests.exceptions.RequestException as e:
            logger.error(f"Canvas API request failed: {e}", exc_info=True)
            return {"success": False, "error": f"Request failed: {e}"}
        except Exception as e:
            logger.error(f"Unexpected error in Canvas API: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _get_next_page_url(self, link_header: str) -> Optional[str]:
        """Parse the Link header to get the next page URL."""
        if not link_header:
            return None

        links = link_header.split(",")
        for link in links:
            parts = link.strip().split(";")
            if len(parts) == 2:
                url = parts[0].strip().strip("<>")
                rel = parts[1].strip()
                if 'rel="next"' in rel or "rel='next'" in rel:
                    return url

        return None

    def post(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a POST request to the Canvas API."""
        import requests

        if not self._ensure_authenticated():
            return {
                "success": False,
                "error": (
                    "Canvas is not configured yet. Ask the user for credentials, "
                    "then call manage_connectors(action='configure', connector_id='canvas', inputs={...}) to connect it directly."
                ),
            }

        url = f"{self._api_base_url}{endpoint}"

        try:
            headers = self._session_headers.copy()
            if files:
                del headers["Content-Type"]

            response = requests.post(
                url,
                headers=headers,
                data=data,
                files=files,
                params=params,
                timeout=60,
            )

            response.raise_for_status()
            return {"success": True, "data": response.json()}

        except requests.exceptions.RequestException as e:
            logger.error(f"Canvas API POST failed: {e}", exc_info=True)
            try:
                error_data = response.json()
                return {"success": False, "error": f"Request failed: {error_data}"}
            except Exception:
                return {"success": False, "error": f"Request failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error in Canvas API POST: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _fuzzy_match_score(self, query: str, candidate: str) -> float:
        """Compute match score between a user search query and a candidate name."""
        import difflib
        import re

        q_clean = re.sub(r'[^a-z0-9\s]', ' ', (query or "").lower()).strip()
        c_clean = re.sub(r'[^a-z0-9\s]', ' ', (candidate or "").lower()).strip()
        if not q_clean or not c_clean:
            return 0.0

        # 1. Exact clean match
        if q_clean == c_clean:
            return 100.0

        # 2. Substring matches
        if q_clean in c_clean:
            return 80.0 + (len(q_clean) / len(c_clean)) * 10.0
        if c_clean in q_clean:
            return 70.0

        # 3. Token-based matching
        q_tokens = set(q_clean.split())
        c_tokens = set(c_clean.split())
        if q_tokens.issubset(c_tokens):
            return 60.0 + (len(q_tokens) / len(c_tokens)) * 10.0

        # 4. Sequence & token overlap similarity
        compact_ratio = difflib.SequenceMatcher(None, q_clean.replace(" ", ""), c_clean.replace(" ", "")).ratio()
        token_ratio = difflib.SequenceMatcher(None, q_clean, c_clean).ratio()
        overlap = len(q_tokens.intersection(c_tokens))
        overlap_score = (overlap / len(q_tokens)) * 40.0 if overlap > 0 else 0.0

        return max(overlap_score, token_ratio * 50.0, compact_ratio * 55.0)

    def resolve_course_id(self, course_identifier: Any) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Resolve a course ID from either an integer/string ID or a course title/code keyword.
        Supports fuzzy and partial matching (e.g. 'Software Project' -> 'CSE-6223 Manage Software Project').
        
        Returns:
            (course_id, course_name, error_message)
        """
        if course_identifier is None or (isinstance(course_identifier, str) and not course_identifier.strip()):
            return None, None, "course (name or ID) is required."
            
        raw_str = str(course_identifier).strip()
        if raw_str.isdigit() and int(raw_str) > 0:
            return int(raw_str), None, None
            
        courses_res = self.get("/courses", params={"enrollment_state": "active", "per_page": 50}, paginate=True)
        if not courses_res.get("success"):
            return None, None, f"Failed to fetch courses: {courses_res.get('error')}"
            
        courses = courses_res.get("data") or []
        if not courses:
            return None, None, "No active courses found on Canvas."
            
        scored_courses = []
        for c in courses:
            cname = str(c.get("name") or "")
            ccode = str(c.get("course_code") or "")
            cid = c.get("id")
            
            score_name = self._fuzzy_match_score(raw_str, cname)
            score_code = self._fuzzy_match_score(raw_str, ccode)
            best_score = max(score_name, score_code)
            
            if best_score > 20.0:
                scored_courses.append((cid, cname, best_score))
                
        if not scored_courses:
            available = ", ".join([f"'{c.get('name')}' (ID: {c.get('id')})" for c in courses[:6]])
            return None, None, f"No active course found matching '{course_identifier}'. Available courses: {available}."
            
        # Sort by score descending
        scored_courses.sort(key=lambda x: x[2], reverse=True)
        best_cid, best_name, best_score = scored_courses[0]
        return best_cid, best_name, None

    def resolve_assignment_id(self, course_id: int, assignment_identifier: Any) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Resolve an assignment ID from either an integer/string ID or an assignment title keyword.
        Supports fuzzy and partial matching (e.g. 'Assignment 4' -> 'Microsoft Project Assignment 4').
        
        Returns:
            (assignment_id, assignment_name, error_message)
        """
        if assignment_identifier is None or (isinstance(assignment_identifier, str) and not assignment_identifier.strip()):
            return None, None, "assignment (name or ID) is required."
            
        raw_str = str(assignment_identifier).strip()
        if raw_str.isdigit() and int(raw_str) > 0:
            return int(raw_str), None, None
            
        assign_res = self.get(f"/courses/{course_id}/assignments", params={"per_page": 50}, paginate=True)
        if not assign_res.get("success"):
            return None, None, f"Failed to fetch course assignments: {assign_res.get('error')}"
            
        assignments = assign_res.get("data") or []
        if not assignments:
            return None, None, f"No assignments found for course {course_id}."
            
        scored_assigns = []
        for a in assignments:
            aname = str(a.get("name") or "")
            aid = a.get("id")
            
            score = self._fuzzy_match_score(raw_str, aname)
            if score > 20.0:
                scored_assigns.append((aid, aname, score))
                
        if not scored_assigns:
            available = ", ".join([f"'{a.get('name')}'" for a in assignments[:6]])
            return None, None, f"No assignment found matching '{assignment_identifier}' in course {course_id}. Available: {available}."
            
        scored_assigns.sort(key=lambda x: x[2], reverse=True)
        best_aid, best_name, best_score = scored_assigns[0]
        return best_aid, best_name, None

    def search_assignments_globally(self, assignment_identifier: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Search for assignments across all active courses concurrently.
        Supports fuzzy matching and numeric assignment IDs.
        
        Returns:
            (matching_assignments, error_message)
            Each assignment in matching_assignments will include:
              'course_id', 'course_name', and assignment fields.
        """
        import concurrent.futures

        if assignment_identifier is None or not str(assignment_identifier).strip():
            return [], "assignment (name or ID) is required."

        raw_str = str(assignment_identifier).strip()
        is_numeric = raw_str.isdigit() and int(raw_str) > 0
        target_id = int(raw_str) if is_numeric else None

        courses_res = self.get("/courses", params={"enrollment_state": "active", "per_page": 50}, paginate=True)
        if not courses_res.get("success"):
            return [], f"Failed to fetch active courses: {courses_res.get('error')}"

        courses = courses_res.get("data") or []
        if not courses:
            return [], "No active courses found on Canvas."

        matches: List[Dict[str, Any]] = []

        def search_course(c: Dict[str, Any]) -> List[Dict[str, Any]]:
            cid = c.get("id")
            cname = c.get("name") or c.get("course_code") or f"Course {cid}"
            found_items = []
            
            if is_numeric and target_id:
                res = self.get(f"/courses/{cid}/assignments/{target_id}")
                if res.get("success") and res.get("data"):
                    item = res["data"]
                    item["course_id"] = cid
                    item["course_name"] = cname
                    item["_match_score"] = 100.0
                    return [item]
            
            res = self.get(f"/courses/{cid}/assignments", params={"search_term": raw_str, "per_page": 20})
            if res.get("success") and res.get("data"):
                for item in res["data"]:
                    item["course_id"] = cid
                    item["course_name"] = cname
                    item["_match_score"] = self._fuzzy_match_score(raw_str, item.get("name", ""))
                    found_items.append(item)
            return found_items

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(courses))) as executor:
            course_results = list(executor.map(search_course, courses))

        for items in course_results:
            matches.extend(items)

        matches.sort(key=lambda x: x.get("_match_score", 0.0), reverse=True)
        return matches, None
