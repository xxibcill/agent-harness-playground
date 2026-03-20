from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from fastapi import HTTPException, Request, status


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


ROLE_ORDER = {
    Role.VIEWER: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
}


@dataclass(frozen=True)
class Identity:
    role: Role


class RequestAuthorizer(Protocol):
    def authorize(self, request: Request) -> Identity | None: ...


class NoAuthAuthorizer:
    def authorize(self, request: Request) -> Identity | None:
        request.state.identity = Identity(role=Role.ADMIN)
        return request.state.identity


class TokenAuthorizer:
    def __init__(self, token_roles: dict[str, Role]) -> None:
        self._token_roles = token_roles

    def authorize(self, request: Request) -> Identity | None:
        required_role = required_role_for_request(request)
        if required_role is None:
            return None

        token = extract_api_token(request)
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API token.",
            )

        actual_role = self._token_roles.get(token)
        if actual_role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token.",
            )

        if ROLE_ORDER[actual_role] < ROLE_ORDER[required_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required_role.value.capitalize()} role required.",
            )

        identity = Identity(role=actual_role)
        request.state.identity = identity
        return identity


def build_authorizer_from_env() -> RequestAuthorizer:
    configured_tokens = os.getenv("AGENT_HARNESS_API_TOKENS", "").strip()
    if not configured_tokens:
        return NoAuthAuthorizer()
    return TokenAuthorizer(parse_token_roles(configured_tokens))


def parse_token_roles(raw_value: str) -> dict[str, Role]:
    token_roles: dict[str, Role] = {}
    for entry in raw_value.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        token, separator, role_name = candidate.partition("=")
        if separator == "" or not token or not role_name:
            raise ValueError(
                "AGENT_HARNESS_API_TOKENS entries must use the format '<token>=<role>'."
            )
        token_roles[token.strip()] = Role(role_name.strip())
    if not token_roles:
        raise ValueError("AGENT_HARNESS_API_TOKENS must contain at least one token.")
    return token_roles


def extract_api_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None

    header_token = request.headers.get("x-agent-harness-token", "").strip()
    if header_token:
        return header_token

    query_token = request.query_params.get("api_token", "").strip()
    return query_token or None


def required_role_for_request(request: Request) -> Role | None:
    path = request.url.path
    method = request.method.upper()

    if path == "/health":
        return None
    if path.startswith("/metrics"):
        return Role.ADMIN
    if method == "GET" and path == "/runs":
        return Role.VIEWER
    if method == "GET" and path.startswith("/runs/"):
        return Role.VIEWER
    if method == "POST" and path == "/runs":
        return Role.OPERATOR
    if method == "POST" and path.endswith("/cancel"):
        return Role.OPERATOR
    if path in {"/openapi.json", "/docs", "/redoc"}:
        return Role.ADMIN
    return None
