"""
Ingestion pipelines for Chad's Unified Personal Search Layer.
Contains components for harvesting Chrome history, Gmail, Drive data, GroupMe messages, and Outlook emails.
"""

from src.core.intelligence.ingestion.base import BaseIngestor
from src.core.intelligence.ingestion.groupme import GroupMeIngestor
from src.core.intelligence.ingestion.outlook import OutlookIngestor

__all__ = ["BaseIngestor", "GroupMeIngestor", "OutlookIngestor"]
