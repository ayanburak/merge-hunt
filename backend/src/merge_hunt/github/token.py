"""
file: token.py
importable: TokenProvider, StaticTokenProvider

GitHubClient never holds a token itself, it asks a TokenProvider before every
request. StaticTokenProvider is the one used today, it is built with a single
token and hands back the same token each time. A provider that serves a
different token per signed in user can replace it without GitHubClient
changing at all.

description: Decides where the GitHub token comes from, separately from the code that uses it.
"""

from abc import ABC, abstractmethod

from pydantic import SecretStr

# --- Token Providers ---

# get_token is async even though StaticTokenProvider has nothing to wait for.
# The web version will look tokens up per user, likely from the database, and
# making the method async later would mean touching every caller.
class TokenProvider(ABC):
    @abstractmethod
    async def get_token(self) -> str:
        """Returns the token to send with the next GitHub request."""

# The token stays wrapped in SecretStr until the very moment it is needed, so
# printing or logging this object never leaks it.
class StaticTokenProvider(TokenProvider):
    def __init__(self, token: SecretStr) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token.get_secret_value()
