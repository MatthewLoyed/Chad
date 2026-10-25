"""Brain-side role verification and atomic setup saving."""
from dataclasses import dataclass
from src.application.commands.base_command import Command


@dataclass
class CheckSetupBundleCommand(Command):
    base_url: str
    api_key: str
    model_id: str
    roles: dict
    confirmed: bool = False

    async def execute(self, facade):
        from src.infrastructure.services.setup_roles import validate_roles
        return await validate_roles(self.base_url, self.api_key, self.model_id, self.roles, self.confirmed)


@dataclass
class SaveSetupBundleCommand(Command):
    base_url: str
    api_key: str
    model_id: str
    token: str
    chat_only: bool = False

    async def execute(self, facade):
        from src.infrastructure.services.setup_roles import save_checked_bundle
        result = save_checked_bundle(self.base_url, self.api_key, self.model_id, self.token, self.chat_only)
        router = getattr(getattr(facade, "_assistant", None), "model_router", None)
        if result.get("success") and router:
            router.reload_models()
        return result
