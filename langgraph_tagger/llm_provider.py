"""Provider routing for structured-output LLM calls.

The model name picks the provider: ``claude-*`` → Anthropic API,
``codex:<model>`` → the local Codex CLI (``codex exec``, billed to the ChatGPT
plan it is logged in with), anything else → OpenAI API. Switching (or rolling
back) is a .env change to ``LLM_MODEL_*`` only.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Generic, Optional, TypeVar

import anthropic
import openai
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# 429 / 5xx / timeout / network on either provider — safe to retry later.
# (APITimeoutError subclasses APIConnectionError in both SDKs.)
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError,
    anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError,
    anthropic.OverloadedError, anthropic.ServiceUnavailableError,
    anthropic.DeadlineExceededError,
)

# Adaptive thinking counts toward max_tokens, so leave headroom over the JSON.
ANTHROPIC_MAX_TOKENS = 16000


CODEX_PREFIX = "codex:"


def is_anthropic(model: str) -> bool:
    return model.startswith("claude-")


def is_codex(model: str) -> bool:
    return model.startswith(CODEX_PREFIX)


def api_key_env(model: str) -> Optional[str]:
    """Env var holding the API key the given model needs (None: Codex CLI login)."""
    if is_codex(model):
        return None
    return "ANTHROPIC_API_KEY" if is_anthropic(model) else "OPENAI_API_KEY"


class CodexExecError(RuntimeError):
    """``codex exec`` exited non-zero or wrote no final message."""


def _codex_bin() -> Optional[str]:
    return os.environ.get("CODEX_BIN") or shutil.which("codex")


def _kill_tree(proc: subprocess.Popen) -> None:
    # On Windows `codex` is an npm shim (cmd → node → codex.exe); killing only
    # the shim would orphan the real process, so kill the whole tree.
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.kill()


async def _run_codex(cmd: list[str], prompt: str, cwd: str) -> tuple[str, str, int]:
    """Run codex exec with the prompt on stdin; returns (stdout, stderr, exit code).

    Popen in a worker thread instead of asyncio subprocesses, which the
    Windows selector event loop does not support. If the caller's timeout
    cancels us, the process tree is killed rather than left running.
    """
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = await asyncio.to_thread(proc.communicate, prompt.encode("utf-8"))
    except BaseException:
        _kill_tree(proc)
        raise
    return (out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
            proc.returncode)


def _json_object(text: str) -> str:
    """Outermost {...} of a reply, tolerating a code fence or stray prose."""
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def _trace_enabled() -> bool:
    return (os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
            and bool(os.environ.get("LANGSMITH_API_KEY")))


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    parsed: Optional[T]
    refusal: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    """One SDK client per provider, each created on first use.

    When LangSmith tracing is on, SDK clients are wrapped so each call shows up
    as a child LLM run under the surrounding LangGraph span.
    """

    def __init__(
        self,
        *,
        openai_api_key: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: int = 2,
        effort: Optional[str] = None,
    ):
        self._keys = {"OPENAI_API_KEY": openai_api_key,
                      "ANTHROPIC_API_KEY": anthropic_api_key}
        self._sdk_kwargs = {"max_retries": max_retries,
                            **({"timeout": timeout} if timeout is not None else {})}
        # Claude thinking depth: low | medium | high | xhigh | max.
        self.effort = effort or os.environ.get("ANTHROPIC_EFFORT", "medium")
        # Codex reasoning depth. "high" matched the OpenAI API's output for
        # gpt-6-luna financial extraction (44 metrics); "medium" gave 28.
        self.codex_effort = os.environ.get("CODEX_REASONING_EFFORT", "high")
        self._openai: Optional[openai.AsyncOpenAI] = None
        self._anthropic: Optional[anthropic.AsyncAnthropic] = None

    def require_key(self, model: str) -> str:
        env = api_key_env(model)
        if env is None:
            if not _codex_bin():
                raise RuntimeError(f"codex CLI not found for model {model}")
            return ""
        key = self._keys[env] or os.environ.get(env)
        if not key:
            raise RuntimeError(f"{env} is required for model {model}")
        return key

    def _openai_client(self, model: str) -> openai.AsyncOpenAI:
        if self._openai is None:
            client = openai.AsyncOpenAI(api_key=self.require_key(model), **self._sdk_kwargs)
            if _trace_enabled():
                try:
                    from langsmith.wrappers import wrap_openai
                    client = wrap_openai(client)
                except ImportError:
                    pass
            self._openai = client
        return self._openai

    def _anthropic_client(self, model: str) -> anthropic.AsyncAnthropic:
        if self._anthropic is None:
            client = anthropic.AsyncAnthropic(api_key=self.require_key(model), **self._sdk_kwargs)
            if _trace_enabled():
                try:
                    from langsmith.wrappers import wrap_anthropic
                    client = wrap_anthropic(client)
                except ImportError:
                    pass
            self._anthropic = client
        return self._anthropic

    async def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: Optional[float] = None,
        constrained: bool = True,
    ) -> StructuredResult[T]:
        """One system+user call whose reply is validated against ``schema``.

        ``temperature`` applies to OpenAI only — Claude models reject
        non-default sampling parameters. ``constrained=False`` gives Claude the
        JSON Schema in the system prompt and lets it write plain JSON instead
        of compiling the schema into a decoding grammar — needed for schemas
        the API rejects as "compiled grammar is too large" (ExtractionResult).
        Non-strict tool input was tried and rejected: Claude sometimes returned
        nested objects as JSON strings. OpenAI ignores the flag. Raises
        pydantic.ValidationError when the reply doesn't validate.
        """
        if is_codex(model):
            return await self._parse_codex(model[len(CODEX_PREFIX):], system, user, schema)
        if is_anthropic(model):
            if constrained:
                return await self._parse_anthropic(model, system, user, schema)
            return await self._parse_anthropic_text(model, system, user, schema)
        return await self._parse_openai(model, system, user, schema, temperature)

    async def _parse_openai(self, model, system, user, schema, temperature):
        completion = await self._openai_client(model).chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=schema,
            **({} if temperature is None else {"temperature": temperature}),
        )
        msg = completion.choices[0].message
        usage = completion.usage
        return StructuredResult(
            parsed=None if msg.refusal else msg.parsed,
            refusal=msg.refusal or None,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    async def _create_anthropic(self, model, system, user, **kwargs):
        return await self._anthropic_client(model).messages.create(
            model=model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            # The system prompt is identical across rows, so cache it: repeat
            # calls within 5 minutes read it at 0.1x the input price.
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            **kwargs,
        )

    @staticmethod
    def _anthropic_result(resp, parsed) -> StructuredResult:
        usage = resp.usage
        tokens_in = (usage.input_tokens
                     + (usage.cache_creation_input_tokens or 0)
                     + (usage.cache_read_input_tokens or 0))
        return StructuredResult(parsed=parsed, input_tokens=tokens_in,
                                output_tokens=usage.output_tokens)

    @staticmethod
    def _refusal_reason(resp) -> str:
        details = getattr(resp, "stop_details", None)
        if details is None:
            return "refusal"
        return f"refusal ({details.category}): {details.explanation or ''}".strip()

    async def _parse_anthropic(self, model, system, user, schema):
        resp = await self._create_anthropic(
            model, system, user,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema",
                           "schema": anthropic.transform_schema(schema)},
            },
        )
        if resp.stop_reason == "refusal":
            return replace(self._anthropic_result(resp, None),
                           refusal=self._refusal_reason(resp))
        # Replies can start with thinking blocks — read text blocks by type.
        text = "".join(b.text for b in resp.content if b.type == "text")
        return self._anthropic_result(resp, schema.model_validate_json(text))

    async def _parse_anthropic_text(self, model, system, user, schema):
        # The full JSON Schema (enums, maxLength, ...) guides the model, and
        # pydantic enforces it below. Appended after the caller's system text
        # so the combined prompt stays identical across calls (cacheable).
        system = (
            f"{system}\n\n<output_format>\n"
            "Reply with one JSON object only (no prose, no code fence) that "
            "validates against this JSON Schema:\n"
            f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            "</output_format>"
        )
        resp = await self._create_anthropic(
            model, system, user, output_config={"effort": self.effort},
        )
        if resp.stop_reason == "refusal":
            return replace(self._anthropic_result(resp, None),
                           refusal=self._refusal_reason(resp))
        text = "".join(b.text for b in resp.content if b.type == "text")
        return self._anthropic_result(resp, schema.model_validate_json(_json_object(text)))

    async def _parse_codex(self, model, system, user, schema):
        self.require_key(CODEX_PREFIX + model)
        # Codex has no system role on the command line, so both parts go in one
        # prompt. The temp dir is the agent's whole (read-only) workspace.
        with tempfile.TemporaryDirectory(prefix="codex-llm-",
                                         ignore_cleanup_errors=True) as tmp:
            schema_path, out_path = Path(tmp, "schema.json"), Path(tmp, "last.json")
            # Same strict schema the OpenAI API path sends.
            strict = openai.pydantic_function_tool(schema)["function"]["parameters"]
            schema_path.write_text(json.dumps(strict, ensure_ascii=False), encoding="utf-8")
            cmd = [
                _codex_bin(), "exec", "-m", model,
                "-c", f"model_reasoning_effort={self.codex_effort}",
                # Skip the user's MCP servers, hooks and notifiers.
                "--ignore-user-config", "--ignore-rules",
                "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                "--output-schema", str(schema_path), "-o", str(out_path),
                "--json", "-",
            ]
            stdout, stderr, code = await _run_codex(cmd, f"{system}\n\n{user}", tmp)
            text = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
            if code != 0 or not text.strip():
                raise CodexExecError(f"codex exec exit {code}: {stderr.strip()[-500:]}")
        usage = {}
        for line in stdout.splitlines():
            if '"turn.completed"' in line:
                usage = json.loads(line).get("usage") or {}
        return StructuredResult(
            parsed=schema.model_validate_json(text),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    async def close(self) -> None:
        for client in (self._openai, self._anthropic):
            if client is not None:
                await client.close()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
