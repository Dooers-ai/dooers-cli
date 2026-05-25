"""HTTP client for the Dooers core API (auth, agent records).

POC scaffold — actual implementation lands in the next milestone.
"""

from dooers_protocol.agents import AgentRecord, CreateAgentRequest
from dooers_protocol.auth import WhoamiResponse


class CoreClient:
    """Thin wrapper around core API endpoints used by the CLI."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    # auth -----------------------------------------------------------------

    def login_request_otp(self, email: str) -> str:
        """POST /session/request — returns the `email_id` for verification."""
        raise NotImplementedError("scaffold")

    def login_verify_otp(self, email_id: str, code: str) -> str:
        """POST /session/create — returns the auth token (cookie value)."""
        raise NotImplementedError("scaffold")

    def whoami(self) -> WhoamiResponse:
        raise NotImplementedError("scaffold")

    def logout(self) -> None:
        raise NotImplementedError("scaffold")

    # agents ---------------------------------------------------------------

    def list_agents(self) -> list[AgentRecord]:
        raise NotImplementedError("scaffold")

    def create_agent(self, req: CreateAgentRequest) -> AgentRecord:
        raise NotImplementedError("scaffold")

    def get_agent(self, agent_id: str) -> AgentRecord:
        raise NotImplementedError("scaffold")
