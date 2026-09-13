"""Read-only local runtime preflight; availability is not scientific validation."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from inspect_ai.model import ChatMessageUser
from jinja2 import TemplateError

from context_fidelity.adapters.model import MODEL_ID, MODEL_REVISION, OfficialTokenizer, Tokenizer
from context_fidelity.adapters.sandbox import (
    DEFAULT_IMAGE,
    CommandRunner,
    SandboxError,
    run_command,
)

CheckName = Literal["tokenizer", "model_health", "model_id", "docker", "sandbox_image"]


class RuntimeNotReady(ValueError):
    """A runtime dependency is unavailable before a phase starts."""


class TokenizerLoader(Protocol):
    def __call__(
        self, *, cache_dir: Path, model_id: str, revision: str, local_files_only: bool
    ) -> Tokenizer: ...


@dataclass(frozen=True)
class RuntimeCheck:
    name: CheckName
    ready: bool
    detail: str


@dataclass(frozen=True)
class RuntimeReadiness:
    checks: tuple[RuntimeCheck, ...]
    tokenizer: Tokenizer | None

    @property
    def ready(self) -> bool:
        return self.tokenizer is not None and all(check.ready for check in self.checks)

    def require_ready(self) -> Tokenizer:
        failures = "; ".join(
            f"{check.name}: {check.detail}" for check in self.checks if not check.ready
        )
        if failures or self.tokenizer is None:
            raise RuntimeNotReady(
                f"Runtime not ready: {failures or 'cached tokenizer unavailable'}"
            )
        return self.tokenizer


def _urls(endpoint: str) -> tuple[str, str]:
    parsed = urlsplit(endpoint)
    # urlsplit defers port validation until this property is read.
    _ = parsed.port
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "local model endpoint must be an unauthenticated HTTP loopback URL "
            "without query or fragment"
        )
    health = urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))
    models = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/models", "", ""))
    return health, models


def _docker_check(
    name: CheckName, command: Sequence[str], runner: CommandRunner, remedy: str
) -> RuntimeCheck:
    try:
        result = runner(command, timeout=5.0, output_limit=16384)
        if result.returncode:
            raise SandboxError(
                result.stderr.decode("utf-8", errors="replace").strip()
                or f"exit {result.returncode}"
            )
        value: object = json.loads(result.stdout)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Docker returned an empty or invalid identity")
        if name == "sandbox_image" and not value.startswith("sha256:"):
            raise ValueError("Docker returned an invalid image ID")
        return RuntimeCheck(name, True, value)
    except (OSError, SandboxError, ValueError) as error:
        return RuntimeCheck(name, False, f"{remedy}: {str(error)[:500]}")


def check_runtime(
    endpoint: str,
    cache_dir: Path,
    *,
    model_id: str = MODEL_ID,
    revision: str = MODEL_REVISION,
    image: str = DEFAULT_IMAGE,
    require_docker: bool = True,
    transport: httpx.BaseTransport | None = None,
    runner: CommandRunner = run_command,
    tokenizer_loader: TokenizerLoader | None = None,
) -> RuntimeReadiness:
    """Check cached tokenization, two non-generating GETs, and optional local Docker.

    The successful tokenizer is returned for reuse. No model generation, token
    download, Docker pull, installation, or provider credential access occurs.
    The model listing cannot attest server revision, precision, or correctness.
    """
    health_url, models_url = _urls(endpoint)
    checks: list[RuntimeCheck] = []
    tokenizer: Tokenizer | None = None
    try:
        loaded = (tokenizer_loader or OfficialTokenizer.load)(
            cache_dir=cache_dir, model_id=model_id, revision=revision, local_files_only=True
        )
        if (
            loaded.count("Runtime check.") <= 0
            or loaded.count_messages([ChatMessageUser(content="Runtime check.")]) <= 0
        ):
            raise ValueError("tokenizer or chat template returned no tokens")
        tokenizer = loaded
        checks.append(
            RuntimeCheck("tokenizer", True, f"cached {model_id}@{revision}; chat template usable")
        )
    except (OSError, ValueError, TypeError, ImportError, RuntimeError, TemplateError) as error:
        checks.append(
            RuntimeCheck(
                "tokenizer",
                False,
                f"Verify the pinned tokenizer cache at {cache_dir}; "
                f"populate it explicitly before running: {error}",
            )
        )
    with httpx.Client(
        timeout=5.0, trust_env=False, follow_redirects=False, transport=transport
    ) as client:
        try:
            client.get(health_url).raise_for_status()
            checks.append(RuntimeCheck("model_health", True, f"endpoint healthy: {health_url}"))
        except httpx.HTTPError as error:
            checks.append(
                RuntimeCheck(
                    "model_health",
                    False,
                    f"Start the local model endpoint and restore its tunnel if needed: {error}",
                )
            )
        try:
            response = client.get(models_url)
            response.raise_for_status()
            payload: object = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError("model endpoint returned an invalid models listing")
            ids = [
                str(row["id"])
                for row in payload["data"]
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            ]
            if model_id not in ids:
                raise ValueError(
                    f"required model {model_id} absent; "
                    f"endpoint reports {', '.join(ids) or 'no models'}"
                )
            checks.append(
                RuntimeCheck("model_id", True, f"serves {model_id}; revision not attested")
            )
        except (httpx.HTTPError, ValueError) as error:
            checks.append(
                RuntimeCheck(
                    "model_id",
                    False,
                    f"Verify the local model endpoint serves the configured model: {error}",
                )
            )
    if require_docker:
        checks.append(
            _docker_check(
                "docker",
                ["docker", "info", "--format", "{{json .ServerVersion}}"],
                runner,
                "Start Docker and select the intended context or DOCKER_HOST",
            )
        )
        checks.append(
            _docker_check(
                "sandbox_image",
                ["docker", "image", "inspect", "--format", "{{json .Id}}", image],
                runner,
                f"Make the pinned sandbox image available in the selected Docker daemon ({image})",
            )
        )
    return RuntimeReadiness(tuple(checks), tokenizer)
