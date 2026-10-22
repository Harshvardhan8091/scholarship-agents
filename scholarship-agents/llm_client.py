from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from config import settings
from event_bus import EventBus

# Keep all provider calls in this process under the same concurrency limit.
_GLOBAL_LLM_SEMAPHORE = asyncio.Semaphore(2)
_CALL_COUNTS_LOCK = threading.Lock()
LLM_CALL_COUNTS: Dict[str, Dict[str, int]] = {"providers": {}, "models": {}}


def reset_call_counts() -> None:
    with _CALL_COUNTS_LOCK:
        LLM_CALL_COUNTS["providers"].clear()
        LLM_CALL_COUNTS["models"].clear()


def _count_call(provider: str, model: str) -> None:
    with _CALL_COUNTS_LOCK:
        LLM_CALL_COUNTS["providers"][provider] = LLM_CALL_COUNTS["providers"].get(provider, 0) + 1
        LLM_CALL_COUNTS["models"][model] = LLM_CALL_COUNTS["models"].get(model, 0) + 1


class LLMClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self._requested_model = model or settings.GEMINI_MODEL
        self.model = self._requested_model
        self._gemini_client: Any = None
        self._groq_client: Any = None
        self.last_provider: Optional[str] = None
        self.provider_call_counts: Dict[str, int] = {}

        if self.api_key:
            try:
                from google import genai

                self._gemini_client = genai.Client(api_key=self.api_key)
            except Exception:
                self._gemini_client = None

        if settings.GROQ_API_KEY:
            try:
                from openai import OpenAI

                self._groq_client = OpenAI(api_key=settings.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
            except Exception:
                self._groq_client = None

    @property
    def is_configured(self) -> bool:
        return bool(
            (settings.GROQ_API_KEY and self._groq_client is not None)
            or (settings.GEMINI_API_KEY and self._gemini_client is not None)
        )

    def _get_provider_order(self) -> List[str]:
        providers = [p.strip().lower() for p in settings.LLM_PROVIDERS.split(",") if p.strip()]
        ordered: List[str] = []
        for provider in providers:
            if provider not in ordered:
                ordered.append(provider)
        if not ordered:
            ordered = ["groq", "gemini"]
        return [p for p in ordered if p in {"groq", "gemini"}]

    def _provider_model(self, provider: str) -> str:
        if provider == "groq":
            return settings.GROQ_MODEL or "openai/gpt-oss-120b"
        return self._requested_model or settings.GEMINI_MODEL or "gemini-2.5-flash"

    def _provider_models(self, provider: str) -> List[str]:
        primary = self._provider_model(provider)
        fallbacks = (
            settings.GROQ_MODEL_FALLBACKS
            if provider == "groq"
            else settings.GEMINI_MODEL_FALLBACKS
        )
        models = [primary]
        models.extend(part.strip() for part in fallbacks.split(",") if part.strip())
        return list(dict.fromkeys(model for model in models if model))

    def _provider_is_available(self, provider: str) -> bool:
        if provider == "groq":
            return bool(settings.GROQ_API_KEY and self._groq_client is not None)
        if provider == "gemini":
            return bool(settings.GEMINI_API_KEY and self._gemini_client is not None)
        return False

    @staticmethod
    def _compute_prompt_hash(prompt: str, system_instruction: Optional[str] = None) -> str:
        content = f"{system_instruction or ''}:::{prompt}".encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def _get_cached_response(self, prompt_hash: str) -> Optional[str]:
        cache_path = settings.LLM_CACHE_DIR / f"{prompt_hash}.json"
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("response")
            except Exception:
                return None
        return None

    def _set_cached_response(self, prompt_hash: str, response: str, model_used: str) -> None:
        cache_path = settings.LLM_CACHE_DIR / f"{prompt_hash}.json"
        temp_path = settings.LLM_CACHE_DIR / f"{prompt_hash}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"hash": prompt_hash, "model": model_used, "response": response}, f, ensure_ascii=False)
            temp_path.replace(cache_path)
        except Exception:
            pass

    @staticmethod
    def _extract_retry_delay(err_str: str, response: Optional[Any] = None) -> Optional[float]:
        if response is not None:
            headers = getattr(response, "headers", {}) or {}
            if hasattr(headers, "get"):
                retry_after = headers.get("retry-after") or headers.get("Retry-After")
                if retry_after is not None:
                    try:
                        return min(float(retry_after), 30.0)
                    except (TypeError, ValueError):
                        try:
                            return min(float(str(retry_after).split(" ", 1)[0]), 30.0)
                        except ValueError:
                            try:
                                retry_time = parsedate_to_datetime(str(retry_after))
                                if retry_time.tzinfo is None:
                                    retry_time = retry_time.replace(tzinfo=timezone.utc)
                                return min(
                                    max((retry_time - datetime.now(timezone.utc)).total_seconds(), 0.0),
                                    30.0,
                                )
                            except (TypeError, ValueError, OverflowError):
                                pass

        m = re.search(r"(?:retry\s+(?:in|after)|wait|retry-after\D+)\s*([0-9]+(?:\.[0-9]+)?)\s*s?", err_str, re.IGNORECASE)
        if m:
            try:
                return min(float(m.group(1)), 30.0)
            except ValueError:
                pass
        return None

    @staticmethod
    def _is_provider_error(err_str: str) -> bool:
        s = err_str.lower()
        return any(token in s for token in ["429", "rate limit", "quota", "timed out", "timeout", "resource_exhausted", "overloaded", "too many requests", "tokens per minute", "per minute"])

    async def _emit_provider_event(
        self,
        event_bus: Optional[EventBus],
        agent_name: str,
        event_type: str,
        message: str,
        provider: str,
        model: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if event_bus:
            payload = {"provider": provider, "model": model, **(details or {})}
            await event_bus.emit(agent=agent_name, event_type=event_type, message=message, details=payload)

    @staticmethod
    def _unsupported_parameter(error: str, parameters: List[str]) -> Optional[str]:
        lowered = error.lower()
        if not any(
            word in lowered
            for word in (
                "unsupported",
                "not supported",
                "unrecognized",
                "unknown parameter",
                "unexpected keyword",
            )
        ):
            return None
        for parameter in parameters:
            if parameter.lower() in lowered:
                return parameter
        return None

    async def _call_groq(
        self,
        prompt: str,
        system_instruction: Optional[str],
        temperature: float,
        max_tokens: int,
        model: str,
        *,
        json_mode: bool,
        event_bus: Optional[EventBus],
        agent_name: str,
    ) -> str:
        if self._groq_client is None:
            raise RuntimeError("Groq client is not available")

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if model.lower().startswith("openai/gpt-oss"):
            payload["max_completion_tokens"] = max(8192, max_tokens)
            payload["reasoning_effort"] = "low"
        else:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        if system_instruction:
            payload["messages"] = [{"role": "system", "content": system_instruction}, {"role": "user", "content": prompt}]

        response = None
        retried_without_parameter = False
        while True:
            _count_call("groq", model)
            try:
                response = await asyncio.to_thread(
                    lambda: self._groq_client.chat.completions.create(**payload)
                )
                break
            except Exception as exc:
                unsupported = self._unsupported_parameter(
                    str(exc),
                    ["response_format", "reasoning_effort", "max_completion_tokens", "max_tokens"],
                )
                if not unsupported or unsupported not in payload or retried_without_parameter:
                    raise
                payload.pop(unsupported)
                retried_without_parameter = True
                await self._emit_provider_event(
                    event_bus,
                    agent_name,
                    "recovery",
                    f"Groq rejected unsupported parameter {unsupported}; retrying without it",
                    "groq",
                    model,
                    {"unsupported_parameter": unsupported},
                )
        text = getattr(response.choices[0].message, "content", "") if getattr(response, "choices", None) else ""
        return text.strip()

    async def _call_gemini(
        self,
        prompt: str,
        system_instruction: Optional[str],
        temperature: float,
        max_tokens: int,
        model: str,
        *,
        json_mode: bool,
        event_bus: Optional[EventBus],
        agent_name: str,
    ) -> str:
        if self._gemini_client is None:
            raise RuntimeError("Gemini client is not available")

        from google.genai import types

        config_values: Dict[str, Any] = {
            "temperature": temperature,
            "system_instruction": system_instruction,
            "max_output_tokens": max_tokens,
        }
        if json_mode:
            config_values["response_mime_type"] = "application/json"

        retried_without_parameter = False
        while True:
            _count_call("gemini", model)
            try:
                config = types.GenerateContentConfig(**config_values)
                response = await asyncio.to_thread(
                    lambda: self._gemini_client.models.generate_content(
                        model=model, contents=prompt, config=config
                    )
                )
                break
            except Exception as exc:
                unsupported = self._unsupported_parameter(str(exc), ["response_mime_type"])
                if not unsupported or unsupported not in config_values or retried_without_parameter:
                    raise
                config_values.pop(unsupported)
                retried_without_parameter = True
                await self._emit_provider_event(
                    event_bus,
                    agent_name,
                    "recovery",
                    f"Gemini rejected unsupported parameter {unsupported}; retrying without it",
                    "gemini",
                    model,
                    {"unsupported_parameter": unsupported},
                )
        return (getattr(response, "text", "") or "").strip()

    @staticmethod
    def _trim_candidate_page_text(prompt: str, max_chars: int = 4000) -> str:
        marker = 'OFFICIAL WEBPAGE TEXT CONTENT:\n"""\n'
        start = prompt.find(marker)
        if start < 0:
            return prompt
        content_start = start + len(marker)
        content_end = prompt.find('\n"""', content_start)
        if content_end < 0 or content_end - content_start <= max_chars:
            return prompt
        return prompt[:content_start] + prompt[content_start:content_start + max_chars] + prompt[content_end:]

    @staticmethod
    def _safe_error(error: str) -> str:
        for key in (settings.GROQ_API_KEY, settings.GEMINI_API_KEY):
            if key:
                error = error.replace(key, "[REDACTED]")
        return error

    async def _generate_text_for_provider(
        self,
        provider: str,
        agent_name: str,
        prompt: str,
        event_bus: Optional[EventBus],
        system_instruction: Optional[str],
        temperature: float,
        max_retries: int,
        *,
        json_mode: bool = False,
    ) -> str:
        models = self._provider_models(provider)
        max_tokens = max(4096, int(getattr(settings, "LLM_MAX_OUTPUT_TOKENS", 4096)))
        last_error = ""
        current_prompt = prompt

        for model_index, model in enumerate(models):
            for attempt in range(1, max_retries + 1):
                try:
                    async with _GLOBAL_LLM_SEMAPHORE:
                        if provider == "groq":
                            text = await self._call_groq(
                                current_prompt, system_instruction, temperature, max_tokens, model,
                                json_mode=json_mode, event_bus=event_bus, agent_name=agent_name,
                            )
                        elif provider == "gemini":
                            text = await self._call_gemini(
                                current_prompt, system_instruction, temperature, max_tokens, model,
                                json_mode=json_mode, event_bus=event_bus, agent_name=agent_name,
                            )
                        else:
                            raise RuntimeError(f"Unsupported provider: {provider}")

                    if not text:
                        if attempt < max_retries and max_tokens < 16384:
                            if model.lower().startswith("openai/gpt-oss"):
                                max_tokens = 16384
                            else:
                                max_tokens = min(max_tokens * 2, 16384)
                            await self._emit_provider_event(
                                event_bus, agent_name, "recovery",
                                f"{provider.title()} returned empty content; retrying with max output tokens {max_tokens}",
                                provider, model, {"max_output_tokens": max_tokens, "attempt": attempt},
                            )
                            continue
                        raise ValueError(f"Empty response from {provider}")

                    self.last_provider = provider
                    self.provider_call_counts[provider] = self.provider_call_counts.get(provider, 0) + 1
                    self.model = model
                    if event_bus:
                        await self._emit_provider_event(
                            event_bus,
                            agent_name,
                            "progress",
                            f"LLM answer received via {provider.title()} using {model}",
                            provider,
                            model,
                            {"attempt": attempt, "provider": provider, "model": model, "response_len": len(text)},
                        )
                    return text
                except Exception as exc:
                    last_error = self._safe_error(str(exc))
                    lower_error = last_error.lower()
                    is_model_missing = "model" in lower_error and any(
                        phrase in lower_error for phrase in ("not found", "does not exist", "unknown model")
                    )
                    is_rate_limited = self._is_provider_error(last_error)
                    if "tokens per minute" in lower_error or "token_per_minute" in lower_error or "tpm" in lower_error:
                        current_prompt = self._trim_candidate_page_text(current_prompt, 4000)

                    if attempt < max_retries and not is_model_missing:
                        delay = self._extract_retry_delay(last_error, getattr(exc, "response", None))
                        if delay is None:
                            delay = 2.0 * attempt if is_rate_limited else 1.0
                        delay = min(delay, 30.0)
                        await self._emit_provider_event(
                            event_bus, agent_name, "recovery",
                            f"{provider.title()} call failed; retrying in {delay:.1f}s ({attempt}/{max_retries})",
                            provider, model,
                            {"error": last_error, "attempt": attempt, "delay": delay},
                        )
                        await asyncio.sleep(delay)
                        continue

                    if model_index + 1 < len(models):
                        next_model = models[model_index + 1]
                        await self._emit_provider_event(
                            event_bus, agent_name, "recovery",
                            f"{provider.title()} model {model!r} failed; switching to {next_model!r}",
                            provider, model,
                            {"error": last_error, "next_model": next_model},
                        )
                        break
                    raise RuntimeError(
                        f"{provider.title()} provider failed for model {model!r} after {attempt} attempt(s): {last_error}"
                    )

        raise RuntimeError(f"{provider.title()} provider failed after all models: {last_error}")

    async def generate_text(
        self,
        agent_name: str,
        prompt: str,
        event_bus: Optional[EventBus] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 3,
    ) -> str:
        prompt_hash = self._compute_prompt_hash(prompt, system_instruction)
        cached_response = self._get_cached_response(prompt_hash)
        if cached_response is not None:
            return cached_response

        provider_order = [p for p in self._get_provider_order() if self._provider_is_available(p)]
        if not provider_order:
            raise RuntimeError("No configured LLM providers are available. Check API keys and startup checks.")

        last_error = ""
        for index, provider in enumerate(provider_order):
            try:
                text = await self._generate_text_for_provider(provider, agent_name, prompt, event_bus, system_instruction, temperature, max_retries)
                self._set_cached_response(prompt_hash, text.strip(), self.model)
                return text.strip()
            except Exception as exc:
                last_error = str(exc)
                next_provider = provider_order[index + 1] if index + 1 < len(provider_order) else None
                if next_provider:
                    fallback_message = f"{provider.title()} failed ({last_error[:120]}), switching to {next_provider.title()}"
                    if self._is_provider_error(last_error):
                        fallback_message = f"{provider.title()} rate-limited, switching to {next_provider.title()}"
                    if event_bus:
                        await self._emit_provider_event(event_bus, agent_name, "recovery", fallback_message, provider, self._provider_model(provider), {"error": last_error, "next_provider": next_provider})
                    continue
                raise RuntimeError(f"LLM generation failed across all providers: {last_error}")

        raise RuntimeError(f"LLM generation failed across all providers: {last_error}")

    async def generate_json(
        self,
        agent_name: str,
        prompt: str,
        event_bus: Optional[EventBus] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 3,
    ) -> Union[Dict[str, Any], List[Any]]:
        provider_order = [p for p in self._get_provider_order() if self._provider_is_available(p)]
        if not provider_order:
            raise RuntimeError("No configured LLM providers are available. Check API keys and startup checks.")

        last_error: Optional[str] = None
        json_prompt = prompt
        if "valid json" not in json_prompt.lower() and "only json" not in json_prompt.lower():
            json_prompt += "\n\nReturn only valid JSON. Do not include markdown fences or prose."
        current_prompt = json_prompt
        json_attempts = min(max(1, max_retries + 1), 4)
        for index, provider in enumerate(provider_order):
            for attempt in range(1, json_attempts + 1):
                raw_text = ""
                try:
                    raw_text = await self._generate_text_for_provider(
                        provider,
                        agent_name,
                        current_prompt,
                        event_bus,
                        system_instruction,
                        temperature,
                        2,
                        json_mode=True,
                    )
                    cleaned = re.sub(
                        r"^\s*```(?:json)?\s*|\s*```\s*$",
                        "",
                        raw_text.strip(),
                        flags=re.IGNORECASE,
                    ).strip()
                    parsed = self._extract_and_parse_json(cleaned)
                    return parsed
                except (json.JSONDecodeError, ValueError) as json_err:
                    last_error = str(json_err)
                    if attempt < json_attempts:
                        if event_bus:
                            await self._emit_provider_event(
                                event_bus,
                                agent_name,
                                "recovery",
                                f"{provider.title()} returned invalid JSON; retrying with error feedback ({attempt}/{json_attempts})",
                                provider,
                                self.model,
                                {"error": last_error, "attempt": attempt, "raw_snippet": raw_text[:300]},
                            )
                        current_prompt = (
                            f"{json_prompt}\n\n"
                            f"CRITICAL FEEDBACK: Your previous response failed JSON parsing with error:\n"
                            f"'{last_error}'\n"
                            f"Please return ONLY valid JSON without markdown fences."
                        )
                        continue
                    if index + 1 < len(provider_order):
                        next_provider = provider_order[index + 1]
                        if event_bus:
                            await self._emit_provider_event(
                                event_bus,
                                agent_name,
                                "recovery",
                                f"{provider.title()} returned invalid JSON after retries, switching to {next_provider.title()}",
                                provider,
                                self._provider_model(provider),
                                {"error": last_error, "next_provider": next_provider},
                            )
                        break
                    raise ValueError(f"LLM returned invalid JSON after {json_attempts} attempts: {last_error}")
                except Exception as exc:
                    last_error = str(exc)
                    if index + 1 < len(provider_order):
                        next_provider = provider_order[index + 1]
                        if event_bus:
                            await self._emit_provider_event(
                                event_bus,
                                agent_name,
                                "recovery",
                                f"{provider.title()} failed ({last_error[:120]}), switching to {next_provider.title()}",
                                provider,
                                self._provider_model(provider),
                                {"error": last_error, "next_provider": next_provider},
                            )
                        break
                    raise
            current_prompt = json_prompt

        raise ValueError(f"Failed to produce JSON after trying all providers: {last_error}")

    @staticmethod
    def _extract_and_parse_json(text: str) -> Union[Dict[str, Any], List[Any]]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        start_brace = text.find("{")
        start_bracket = text.find("[")

        if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
            end_brace = text.rfind("}")
            if end_brace != -1 and end_brace > start_brace:
                candidate = text[start_brace : end_brace + 1]
                return json.loads(candidate)

        if start_bracket != -1:
            end_bracket = text.rfind("]")
            if end_bracket != -1 and end_bracket > start_bracket:
                candidate = text[start_bracket : end_bracket + 1]
                return json.loads(candidate)

        raise json.JSONDecodeError("No valid JSON structure found in output", text, 0)
