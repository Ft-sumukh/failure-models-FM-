"""LLM target adapter: a real code model as the system under test.

The hand-written targets in ``targets.py`` exist so the instrument can be
validated offline. This module is the other half: a real model answering real
task contracts, with its output fed to the same exogenous verifier.

Design constraints, in priority order:

1. **The model never sees the oracle.** Prompts carry the task contract and
   nothing about expected outputs. A target that is told the answer cannot
   produce a finding.
2. **Every generation is recorded.** model id, prompt hash, decoding settings,
   raw response. Without these a run cannot be reproduced or audited, and a
   result that cannot be reproduced is not a result.
3. **Failures are failures.** A provider error is not a code failure and must
   not land in the failure corpus. It is counted separately, because conflating
   infrastructure with the target is how a pipeline reports its own
   infrastructure as model defects.
4. **Cost and rate limits are visible.** Enumerating thousands of probes
   against a paid API is how an experiment quietly becomes unaffordable.

Retry is deliberately absent. A retried generation is a different sample, and
in an experiment that measures sampling behaviour, silently swapping samples
corrupts the measurement. Transport errors are surfaced instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

#: Prompt template. Note what is absent: the oracle, the expected output, and
#: any hint about the failure being hunted. The contract is the whole prompt.
PROMPT_TEMPLATE = """\
You are implementing a single Python function.

Contract:
{contract}

Requirements:
- Define exactly one top-level function named `solution`.
- It takes one argument, a list of integers, and returns the result.
- It must not raise on any input described by the contract.
- Do not print. Do not read input. Return the value directly.
- Reply with the function only: no explanation, no tests, no example usage.

Write the function now.
"""


class ProviderError(RuntimeError):
    """Transport, auth, or rate-limit failure. Never a code defect."""


class EmptyResponse(ProviderError):
    """The provider returned no answer at all.

    Distinct from a model failure. A reasoning model given a tight
    ``max_tokens`` will spend the entire budget on its reasoning trace and
    return ``content: null`` with the code nowhere in the response. The
    verifier would score that empty string as a SyntaxError and the corpus
    would record a defect the model never committed -- qwen3.5-9b produced
    three of those before this was caught.

    It is a harness configuration error, so it is raised rather than scored.
    """


@dataclass
class Generation:
    """One model response plus everything needed to reproduce it."""

    model_id: str
    prompt_hash: str
    code: str
    raw_response: str
    finish_reason: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_record_fields(self) -> dict:
        return {
            "model_id": self.model_id,
            "prompt_hash": self.prompt_hash,
            "decoding": {"temperature": 0, "seed": 0},
            "output_ref": None,
        }


@dataclass
class TargetStats:
    """Provider-side accounting, kept strictly separate from defect counts."""

    requests: int = 0
    successes: int = 0
    provider_errors: int = 0
    unparseable: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latencies_ms: list[int] = field(default_factory=list)

    def record(self, gen: Generation | None, error: bool = False) -> None:
        self.requests += 1
        if error or gen is None:
            self.provider_errors += 1
            return
        self.successes += 1
        self.input_tokens += gen.input_tokens
        self.output_tokens += gen.output_tokens
        self.cost_usd += gen.cost_usd
        self.latencies_ms.append(gen.latency_ms)

    @property
    def error_rate(self) -> float:
        return self.provider_errors / max(1, self.requests)

    def summary(self) -> str:
        med = 0
        if self.latencies_ms:
            s = sorted(self.latencies_ms)
            med = s[len(s) // 2]
        return (
            f"requests={self.requests} ok={self.successes} "
            f"provider_errors={self.provider_errors} "
            f"unparseable={self.unparseable} "
            f"tokens={self.input_tokens + self.output_tokens} "
            f"cost=${self.cost_usd:.4f} median_latency={med}ms"
        )


def _extract_code(raw: str | None) -> str:
    """Pull a function out of a model response.

    Models wrap code in fences, prefix it with prose, or both. A target whose
    failure is 'wrote prose instead of code' is a real failure and must be
    recorded as one -- so this returns the best available code candidate and
    lets the verifier decide, rather than raising here.

    ``raw`` may be None. Reasoning models return ``content: null`` and put the
    output in ``reasoning`` instead, which is a provider shape difference and
    not a model failure. Treating it as a crash took down a whole live run, so
    the reasoning field is read as a fallback and a genuinely empty response
    yields an empty candidate that the verifier scores as a defect.
    """
    if not raw:
        return ""
    text = raw.strip()

    if "```" in text:
        blocks = text.split("```")
        for block in blocks:
            b = block.strip()
            if b.startswith("python"):
                b = b[len("python"):].strip()
            if "def solution" in b:
                return b

    # No fence, or no fenced block contained the function: find the definition
    # directly and keep it whole.
    idx = text.find("def solution")
    if idx == -1:
        return text
    return text[idx:]


class LLMTarget:
    """A code model queried over an OpenAI-compatible chat completions API."""

    def __init__(
        self,
        model_id: str,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: int = 60,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ):
        self.model_id = model_id
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.temperature = temperature
        # Sent explicitly. Without it the provider assumes its model maximum
        # (65536 for some models), which trips a credit-based cap and fails
        # with HTTP 402 on a request that would otherwise succeed. A single
        # short function does not need 64k tokens of headroom.
        self.max_tokens = max_tokens
        self.stats = TargetStats()
        if not self.api_key:
            raise ProviderError(
                "no API key: set OPENROUTER_API_KEY or pass api_key explicitly"
            )

    def _prompt(self, contract: str) -> tuple[str, str]:
        text = PROMPT_TEMPLATE.format(contract=contract)
        return text, hashlib.sha256(text.encode()).hexdigest()

    def generate(self, contract: str) -> Generation:
        prompt, prompt_hash = self._prompt(contract)
        body = json.dumps(
            {
                "model": self.model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "seed": 7,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            self.stats.record(None, error=True)
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            self.stats.record(None, error=True)
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        raw = message.get("content")
        reasoning = message.get("reasoning") or ""
        finish = choice.get("finish_reason", "unknown")

        # A reasoning model that spent its whole budget thinking returns
        # content: null and finish_reason 'length'. That is a harness
        # configuration error, not a model defect, and scoring it as a
        # SyntaxError writes a fabricated failure into the corpus.
        if finish == "length" and not raw:
            self.stats.record(None, error=True)
            raise EmptyResponse(
                f"{self.model_id} exhausted max_tokens={self.max_tokens} while "
                f"reasoning and returned no answer ({len(reasoning)} chars of "
                "reasoning). Raise --max-tokens or use a non-reasoning model."
            )
        if not raw:
            raw = reasoning or ""
        usage = payload.get("usage") or {}

        gen = Generation(
            model_id=self.model_id,
            prompt_hash=prompt_hash,
            code=_extract_code(raw),
            raw_response=raw,
            finish_reason=choice.get("finish_reason", "unknown"),
            latency_ms=latency_ms,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cost_usd=float(usage.get("cost", 0) or 0),
        )
        self.stats.record(gen)
        if "def solution" not in gen.code:
            self.stats.unparseable += 1
        return gen


def probe_cost_estimate(
    n_probes: int, samples_per_probe: int = 1, cost_per_call: float = 0.002
) -> dict:
    """Cost envelope, because a paid experiment needs one before it runs."""
    calls = n_probes * samples_per_probe
    return {
        "probes": n_probes,
        "samples_per_probe": samples_per_probe,
        "total_calls": calls,
        "estimated_cost_usd": round(calls * cost_per_call, 4),
    }
