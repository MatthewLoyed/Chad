"""Desktop bridge for the independent tool selector setting."""
from src.application.commands import GetToolSelectorModelCommand, SetToolSelectorModelCommand


class ToolSelectorAPIMixin:
    def get_tool_selector_model(self):
        return self._execute_command(GetToolSelectorModelCommand(), timeout=5.0,
                                     default_fallback=None)

    def set_tool_selector_model(self, model_id, provider="lmstudio"):
        return self._execute_command(SetToolSelectorModelCommand(model_id=model_id, provider=provider),
                                     timeout=10.0, default_fallback=False)

