"""All LLM calls go through here.

Brain is configurable via config.json `brain` section (see below) with two
profiles: "omni" (OmniRoute router, OpenAI-compatible endpoint, local :20128)
and "claude" (Anthropic API). SKY_BRAIN env var picks the profile.
Responses are always normalized to the OpenAI-style dict
({content, tool_calls}) so callers never care which brain ran.

Degrades gracefully: raises LLMDown instead of crashing the loop.
Never logs or stores secrets. Keys come from the environment / ~/sky/.env only.
"""
import json
import logging
import os
import re
from pathlib import Path

from openai import OpenAI

log = logging.getLogger("sky.llm")

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_env_loaded = False


def _load_env_file():
    """Tiny .env loader (KEY=VALUE lines, no quoting rules) — no dependency."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    if not _ENV_FILE.exists():
        return
    try:
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
    except Exception as e:  # unreadable .env must not kill the brain
        log.warning("could not read .env: %s", e)


_client = None
_client_key = None  # (base_url, api_key) the client was built with
_anthropic = None


class LLMDown(Exception):
    """Brain unreachable or returned nothing usable."""


def brain_profile(cfg: dict) -> dict:
    """Resolve the active brain profile from config + SKY_BRAIN override."""
    brain = cfg.get("brain") or {}
    profiles = brain.get("profiles") or {}
    active = os.environ.get("SKY_BRAIN") or brain.get("active") or "omni"
    p = profiles.get(active)
    if p is None:  # fall back to the legacy flat config keys
        p = {"kind": "openai", "base_url": cfg.get("base_url"),
             "model": cfg.get("model"), "api_key_env": cfg.get("api_key_env",
                                                              "OMNIROUTE_API_KEY")}
    return dict(p, name=active)


def _get_client(base_url: str, api_key: str) -> OpenAI:
    global _client, _client_key
    if _client is None or _client_key != (base_url, api_key):
        _client = OpenAI(base_url=base_url, api_key=api_key, timeout=90.0)
        _client_key = (base_url, api_key)
    return _client


def _get_anthropic(api_key: str):
    global _anthropic
    if _anthropic is None or getattr(_anthropic, "sky_key", None) != api_key:
        from anthropic import Anthropic
        _anthropic = Anthropic(api_key=api_key, timeout=90.0)
        _anthropic.sky_key = api_key
    return _anthropic


def _clean(text: str) -> str:
    """Strip reasoning-model artifacts (GLM sometimes emits <think>...</think>)."""
    return re.sub(r"setq.*?/setq", "", text, flags=re.DOTALL).strip()


# --- Anthropic <-> OpenAI shape conversion ---------------------------------

def _tools_to_anthropic(tools: list) -> list:
    out = []
    for t in tools or []:
        f = t.get("function", t)
        out.append({"name": f["name"],
                    "description": f.get("description", ""),
                    "input_schema": f.get("parameters",
                                          {"type": "object", "properties": {}})})
    return out


def _messages_to_anthropic(messages: list) -> tuple[str, list]:
    """Split OpenAI messages into (system, [anthropic messages])."""
    system = []
    conv = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system.append(m.get("content") or "")
        elif role == "tool":
            conv.append({"role": "user", "content": [{"type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content") or ""}]})
        elif role == "assistant" and m.get("tool_calls"):
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                args = tc["function"].get("arguments") or "{}"
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc["id"],
                               "name": tc["function"]["name"],
                               "input": args})
            conv.append({"role": "assistant", "content": blocks})
        else:
            conv.append({"role": "assistant" if role == "assistant" else "user",
                         "content": m.get("content") or ""})
    return "\n\n".join(system), conv


def _anthropic_raw(cfg: dict, messages: list, tools: list | None,
                   mt: int, temp: float, mdl: str) -> dict:
    api_key = os.environ.get(_brain_api_key_env(cfg), "")
    if not api_key:
        raise LLMDown("claude brain selected but %s is not set"
                      % _brain_api_key_env(cfg))
    system, conv = _messages_to_anthropic(messages)
    kwargs = {"model": mdl, "messages": conv, "max_tokens": mt,
              "temperature": temp, "system": system}
    if tools:
        kwargs["tools"] = _tools_to_anthropic(tools)
    try:
        resp = _get_anthropic(api_key).messages.create(**kwargs)
    except LLMDown:
        raise
    except Exception as e:
        log.error("claude call failed: %s", e)
        raise LLMDown(str(e)) from e
    content, tool_calls = "", []
    for block in resp.content:
        if block.type == "text":
            content += block.text
        elif block.type == "tool_use":
            tool_calls.append({"id": block.id, "type": "function",
                               "function": {"name": block.name,
                                            "arguments": json.dumps(block.input)}})
    content = _clean(content).strip()
    if not content and not tool_calls:
        raise LLMDown("claude returned no answer content")
    log.info("claude ok model=%s chars=%d tool_calls=%d",
             mdl, len(content), len(tool_calls))
    out = {"content": content}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _brain_api_key_env(profile: dict) -> str:
    return os.environ.get("SKY_API_KEY_ENV") or profile.get("api_key_env",
                                                            "OMNIROUTE_API_KEY")


# --- public API -------------------------------------------------------------

def chat_raw(cfg: dict, messages: list, tools: list | None = None,
             model: str | None = None, max_tokens: int | None = None,
             temperature: float | None = None) -> dict:
    """One completion. Returns the assistant message as a plain dict
    ({content, tool_calls}). Raises LLMDown on failure."""
    _load_env_file()
    profile = brain_profile(cfg)
    mt = max_tokens or cfg.get("max_tokens", 900)
    temp = temperature if temperature is not None else cfg.get("temperature", 0.6)
    mdl = model or os.environ.get("SKY_MODEL") or profile.get("model")
    if profile.get("kind") == "anthropic":
        return _anthropic_raw(cfg, messages, tools, mt, temp, mdl)

    base_url = os.environ.get("SKY_BASE_URL", profile.get("base_url"))
    api_key = os.environ.get(_brain_api_key_env(profile), "local")
    kwargs = {"model": mdl, "messages": messages, "max_tokens": mt,
              "temperature": temp}
    if tools:
        kwargs["tools"] = tools
    if cfg.get("disable_thinking"):
        # GLM-style reasoning models burn 10s+ thinking; skip it for snappy chat.
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    def _create():
        try:
            return _get_client(base_url, api_key).chat.completions.create(**kwargs)
        except Exception as first:
            if kwargs.pop("extra_body", None) is not None:
                try:  # router may reject the thinking flag — retry plain
                    return _get_client(base_url, api_key).chat.completions.create(**kwargs)
                except Exception:
                    raise first from None
            raise

    try:
        resp = _create()
    except Exception as e:  # router down, timeout, bad model — try the fallback brain model
        fb = profile.get("fallback_model")
        if fb and fb != mdl and not model:  # explicit model= arg means caller knows what it wants
            log.warning("LLM %s failed (%s) — trying fallback %s", mdl, str(e)[:120], fb)
            try:
                kwargs["model"] = fb
                resp = _get_client(base_url, api_key).chat.completions.create(**kwargs)
                mdl = fb
            except Exception as e2:
                log.error("LLM fallback also failed: %s", e2)
                raise LLMDown(str(e2)) from e2
        else:
            log.error("LLM call failed: %s", e)
            raise LLMDown(str(e)) from e

    msg = resp.choices[0].message
    content = _clean(msg.content or "")

    if not content and not (msg.tool_calls or []):
        # Reasoning model burned the whole budget on thinking. Retry once with room.
        log.info("empty content (reasoning ate budget), retrying with max_tokens=%d", mt * 2)
        try:
            kwargs["max_tokens"] = mt * 2
            resp = _get_client(base_url, api_key).chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            content = _clean(msg.content or "")
        except Exception as e:
            log.error("LLM retry failed: %s", e)
            raise LLMDown(str(e)) from e

    if not content and not (msg.tool_calls or []):
        raise LLMDown("model returned no answer content")

    log.info("LLM ok brain=%s model=%s chars=%d tool_calls=%d",
             profile["name"], mdl, len(content or ""), len(msg.tool_calls or []))
    out = {"content": content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name,
                          "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return out


def chat(cfg: dict, messages: list, max_tokens: int | None = None,
         temperature: float | None = None) -> str:
    """One completion. Returns reply text. Raises LLMDown on failure."""
    return chat_raw(cfg, messages, max_tokens=max_tokens,
                    temperature=temperature)["content"]
