import os


def _load_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


class _Settings:
    @property
    def api_key(self) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to a .env file or export it in your shell."
            )
        return key

    @property
    def model(self) -> str:
        return os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")

    @property
    def max_tokens(self) -> int:
        return int(os.environ.get("MAX_TOKENS", "1024"))

    @property
    def max_tool_steps(self) -> int:
        return int(os.environ.get("MAX_TOOL_STEPS", "10"))


settings = _Settings()
