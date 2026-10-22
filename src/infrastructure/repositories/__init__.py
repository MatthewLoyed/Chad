"""
Infrastructure Repositories

Data access layer that abstracts persistence mechanisms.
Repositories provide clean interfaces for data operations.
"""

from .memory_repository import MemoryRepository
from .config_repository import ConfigRepository
from .task_repository import TaskRepository

__all__ = [
    'MemoryRepository',
    'ConfigRepository',
    'TaskRepository'
]
