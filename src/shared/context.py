from pydantic import BaseModel
from typing import Optional

class RequestContext(BaseModel):
    """
    Explicit context passed down through the tool execution pipeline.
    Ensures that tools (like cron schedulers) can record where tasks originated.
    """
    platform: str
    session_id: str
    user_id: Optional[str] = None
    turn_id: Optional[str] = None
    category: Optional[str] = None
    delivery: Optional[str] = None
    # Domain-agent scope for this turn (e.g. "financial"), resolved server-side from the
    # conversation row. None = the general tier. NOTE this is unrelated to `category` above,
    # which is the turn lane/delivery mode -- see src/shared/domain_scope.py.
    domain: Optional[str] = None
    retry: bool = False
    keep_count: Optional[int] = None

