"""
Base Ingestor Definition
Defines the standard interface for all data sources feeding into Chad's Unified Vector Base.
"""

import abc
import logging
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger("Ingestion")

class BaseIngestor(abc.ABC):
    """
    Base interface for personal knowledge base ingestors.
    Handles data retrieval, metadata normalization, and prepares items for vector memory storage.
    """
    
    @abc.abstractmethod
    def get_source_name(self) -> str:
        """Return the unique source identifier (e.g. 'chrome', 'gmail', 'drive')."""
        pass
        
    @abc.abstractmethod
    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Retrieve, normalize, and return items from the source since the last sync.
        
        Args:
            last_sync: The datetime of the last successful sync. Only items newer than this
                       should be fetched for performance and token efficiency.
                       
        Returns:
            List of normalized dictionaries containing:
              - 'content': (str) The string payload to be embedded and searched.
              - 'importance': (float) The decay/importance value between 0.0 and 1.0.
              - 'metadata': (dict) Arbitrary payloads for hashing, source tracing, and deep recall.
        """
        pass

    def compute_hash(self, value: str) -> str:
        """Generates a stable MD5 hash of a string to prevent duplicate embeddings."""
        if not value:
            return ""
        return hashlib.md5(value.encode('utf-8')).hexdigest()
