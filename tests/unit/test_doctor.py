"""Runtime availability checks use bounded local HTTP and trusted Docker boundaries."""

from pathlib import Path

import httpx
import pytest
from inspect_ai.model import ChatMessage

from context_fidelity import doctor
from context_fidelity.adapters.model import MODEL_ID, MODEL_REVISION
from context_fidelity.adapters.sandbox import DEFAULT_IMAGE, CommandResult, SandboxError


class CachedTokenizer:
    def count(self, text: str) -> int:
        return len(text.split())

    def count_messages(self, messages: list[ChatMessage], tools: object = ()) -> int:
        return 12


def docker_ready(command, **kwargs):
    assert kwargs == {"timeout": 5.0, "output_limit": 16384}
    if command == ["docker", "info", "--format", "{{json .ServerVersion}}"]:
        return CommandResult(0, b'"28.3.2"\n')
    assert command == ["docker", "image", "inspect", "--format", "{{json .Id}}", DEFAULT_IMAGE]
    return CommandResult(0, b'"sha256:0123456789abcdef"\n')


def healthy(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    assert "authorization" not in request.headers
    assert request.extensions["timeout"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}
    if request.url.path == "/health":
        return httpx.Response(200)
    assert request.url.path == "/v1/models"
    return httpx.Response(200, json={"data": [{"id": MODEL_ID}]})


def check(tmp_path: Path, **kwargs):
    return doctor.check_runtime(
        "http://127.0.0.1:18081/v1",
        tmp_path / "hf",
        transport=httpx.MockTransport(kwargs.pop("handler", healthy)),
        runner=kwargs.pop("runner", docker_ready),
        tokenizer_loader=kwargs.pop("tokenizer_loader", lambda **args: CachedTokenizer()),
        **kwargs,
    )


def test_doctor_checks_cached_template_model_health_and_local_image_once(tmp_path: Path) -> None:
    loaded = []
    tokenizer = CachedTokenizer()

    def load(**kwargs):
        loaded.append(kwargs)
        return tokenizer

    result = check(tmp_path, tokenizer_loader=load)
    assert result.ready
    assert result.require_ready() is tokenizer
    assert loaded == [
        {
            "cache_dir": tmp_path / "hf",
            "model_id": MODEL_ID,
            "revision": MODEL_REVISION,
            "local_files_only": True,
        }
    ]
    checks = {check.name: check for check in result.checks}
    assert set(checks) == {"tokenizer", "model_health", "model_id", "docker", "sandbox_image"}
    assert "28.3.2" in checks["docker"].detail
    assert "sha256:0123456789abcdef" in checks["sandbox_image"].detail
    assert MODEL_ID in checks["model_id"].detail
    assert "revision not attested" in checks["model_id"].detail


@pytest.mark.parametrize(
    "failure", ["unreachable", "unhealthy", "wrong_model", "invalid_models", "redirect"]
)
def test_model_readiness_fails_actionably_without_generation_or_redirects(
    tmp_path: Path, failure: str
) -> None:
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if failure == "unreachable":
            raise httpx.ConnectError("tunnel closed", request=request)
        if request.url.path == "/health":
            if failure == "unhealthy":
                return httpx.Response(503)
            if failure == "redirect":
                return httpx.Response(302, headers={"location": "https://outside.invalid/health"})
            return httpx.Response(200)
        if failure == "wrong_model":
            return httpx.Response(200, json={"data": [{"id": "other-model"}]})
        if failure == "invalid_models":
            return httpx.Response(200, json={"data": "invalid"})
        return healthy(request)

    result = check(tmp_path, handler=handler)
    assert not result.ready
    with pytest.raises(doctor.RuntimeNotReady, match="Runtime not ready"):
        result.require_ready()
    assert requested == ["http://127.0.0.1:18081/health", "http://127.0.0.1:18081/v1/models"]
    assert any(
        not item.ready and ("endpoint" in item.detail or "model" in item.detail)
        for item in result.checks
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1/v1",
        "http://example.com/v1",
        "http://user@127.0.0.1/v1",
        "http://127.0.0.1/v1?key=secret",
        "http://127.0.0.1/v1#fragment",
        "http://127.0.0.1:notaport/v1",
        "http://127.0.0.1:99999/v1",
        "http://127.0.0.1:-1/v1",
    ],
)
def test_invalid_endpoint_is_rejected_before_loading_or_io(tmp_path: Path, endpoint: str) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("invalid endpoint performed I/O")

    with pytest.raises(ValueError, match="unauthenticated HTTP loopback|port|Port"):
        doctor.check_runtime(
            endpoint,
            tmp_path,
            transport=httpx.MockTransport(forbidden),
            runner=forbidden,
            tokenizer_loader=forbidden,
        )


@pytest.mark.parametrize("failure", ["missing", "template", "empty"])
def test_invalid_cached_tokenizer_is_an_explicit_readiness_failure(
    tmp_path: Path, failure: str
) -> None:
    class BrokenTokenizer(CachedTokenizer):
        def count_messages(self, messages, tools=()):
            if failure == "template":
                raise ValueError("chat template unavailable")
            return 0

    def load(**kwargs):
        assert kwargs["local_files_only"] is True
        if failure == "missing":
            raise OSError("cached tokenizer missing")
        return BrokenTokenizer()

    result = check(tmp_path, tokenizer_loader=load)
    assert not result.ready
    tokenizer_check = next(item for item in result.checks if item.name == "tokenizer")
    assert not tokenizer_check.ready and "cache" in tokenizer_check.detail
    with pytest.raises(doctor.RuntimeNotReady, match="tokenizer"):
        result.require_ready()


@pytest.mark.parametrize(
    "failure",
    [
        "docker_down",
        "missing_image",
        "timeout",
        "missing_cli",
        "invalid_json",
        "empty_identity",
        "invalid_image_id",
        "empty_failure_output",
    ],
)
def test_docker_preflight_preserves_specific_failed_check(tmp_path: Path, failure: str) -> None:
    def runner(command, **kwargs):
        if failure == "timeout":
            raise SandboxError("command timeout")
        if failure == "missing_cli":
            raise FileNotFoundError("docker executable absent")
        if failure == "docker_down" and command[1] == "info":
            return CommandResult(1, stderr=b"cannot connect to Docker daemon")
        if failure == "missing_image" and command[1] == "image":
            return CommandResult(1, stderr=b"No such image")
        if failure == "invalid_json":
            return CommandResult(0, b"invalid")
        if failure == "empty_identity":
            return CommandResult(0, b"null")
        if failure == "invalid_image_id" and command[1] == "image":
            return CommandResult(0, b'"not-an-image-ID"')
        if failure == "empty_failure_output":
            return CommandResult(1)
        return docker_ready(command, **kwargs)

    result = check(tmp_path, runner=runner)
    assert not result.ready
    with pytest.raises(doctor.RuntimeNotReady, match="Docker|image"):
        result.require_ready()
    checks = {item.name: item for item in result.checks}
    if failure == "missing_image":
        assert checks["docker"].ready and not checks["sandbox_image"].ready
    if failure == "docker_down":
        assert not checks["docker"].ready and checks["sandbox_image"].ready


def test_reporting_runtime_does_not_require_or_contact_docker(tmp_path: Path) -> None:
    def absent_docker(*args, **kwargs):
        pytest.fail("reporting contacted Docker")

    result = check(tmp_path, require_docker=False, runner=absent_docker)
    assert result.ready and result.require_ready() is result.tokenizer
    assert {item.name for item in result.checks} == {"tokenizer", "model_health", "model_id"}


def test_default_cached_loader_is_reused_and_http_client_ignores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Backend:
        def encode(self, text, *, add_special_tokens):
            assert not add_special_tokens
            return [1, 2]

        def apply_chat_template(self, conversation, *, tools, tokenize, add_generation_prompt):
            assert conversation == [{"role": "user", "content": "Runtime check."}]
            assert tools is None and tokenize and add_generation_prompt
            return [1, 2, 3]

    tokenizer = doctor.OfficialTokenizer(Backend())
    loaded = []

    def load(**kwargs):
        loaded.append(kwargs)
        return tokenizer

    monkeypatch.setattr(doctor.OfficialTokenizer, "load", load)
    client = httpx.Client

    def local_client(**kwargs):
        assert kwargs["timeout"] == 5.0
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return client(**kwargs)

    monkeypatch.setattr(doctor.httpx, "Client", local_client)
    result = doctor.check_runtime(
        "http://[::1]:18081/v1/",
        tmp_path / "hf",
        transport=httpx.MockTransport(healthy),
        runner=docker_ready,
    )
    assert result.require_ready() is tokenizer
    assert loaded == [
        {
            "cache_dir": tmp_path / "hf",
            "model_id": MODEL_ID,
            "revision": MODEL_REVISION,
            "local_files_only": True,
        }
    ]
