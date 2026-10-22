"""
DateTime Info Tool - Get current time and date
"""

from datetime import datetime
from typing import Dict, Any

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.tools.display_helpers import format_get_datetime

logger = setup_logger("DateTimeTool")

class DateTimeTool(BaseTool):
    """Tool for getting current date and time information."""
    
    name = "get_datetime"
    description = "Get the current date and/or time"
    category = "system"
    
    def format_action(self, params: Dict[str, Any]) -> str:
        return format_get_datetime(params)

    def get_schema(self) -> Dict[str, Any]:
        """Return the function schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["time", "date", "datetime", "timestamp"],
                        "description": "What to return (time/date/datetime/timestamp)"
                    }
                },
                "required": ["format"]
            }
        }
    
    async def execute(self, format: str = "datetime", **kwargs) -> Dict[str, Any]:
        """
        Get current date/time.
        
        Args:
            format: What to return (time/date/datetime/timestamp)
            
        Returns:
            Result dictionary with success status and formatted time
        """
        try:
            now = datetime.now()
            
            if format == "time":
                result = now.strftime("%I:%M %p")  # 12-hour format with AM/PM
            elif format == "date":
                result = now.strftime("%B %d, %Y")  # Month Day, Year
            elif format == "datetime":
                result = now.strftime("%I:%M %p on %B %d, %Y")
            elif format == "timestamp":
                result = now.isoformat()
            else:
                return {
                    "success": False,
                    "error": f"Unknown format: {format}"
                }

            return {
                "success": True,
                "result": result,
                "format": format,
                "caption": f"System Time: {result}"
            }
            
        except Exception as e:
            logger.error(f"Failed to get datetime: {e}")
            return {
                "success": False,
                "error": f"Failed to get datetime: {str(e)}"
            }
    
    def validate_params(self, format: str = None, **kwargs) -> tuple[bool, str | None]:
        """Validate parameters."""
        if format and format not in ["time", "date", "datetime", "timestamp"]:
            return False, "format must be one of: time, date, datetime, timestamp"
        return True, None

