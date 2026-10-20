from __future__ import annotations

import asyncio
import json
import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import settings
from event_bus import EventBus

# Global concurrency semaphore across LLM calls to enforce budget (default 2)
_GLOBAL_LLM_SEMAPHORE = asyncio.Semaphore(settings.LLM_CONCURRENCY)


class LLMClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_MODEL
        self._client: Optional[genai.Client] = None
        if self.api_key:
            try:
                self._client = genai.Client(api_key=self.api_key)
            except Exception:
                self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self._client is not None)

    def _get_models_chain(self) -> List[str]:
        raw_fallbacks = [
            m.strip()
            for m in settings.GEMINI_MODEL_FALLBACKS.split(",")
            if m.strip()
        ]
        chain: List[str] = []
        if self.model and self.model not in chain:
            chain.append(self.model)
        for m in raw_fallbacks:
            if m not in chain:
                chain.append(m)
        return chain

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

    def _set_cached_response(
        self, prompt_hash: str, response: str, model_used: str
    ) -> None:
        cache_path = settings.LLM_CACHE_DIR / f"{prompt_hash}.json"
        temp_path = settings.LLM_CACHE_DIR / f"{prompt_hash}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "hash": prompt_hash,
                        "model": model_used,
                        "response": response,
                    },
                    f,
                    ensure_ascii=False,
                )
            temp_path.replace(cache_path)
        except Exception:
            pass

    @staticmethod
    def _extract_retry_delay(err_str: str) -> Optional[float]:
        """Extracts retry delay returned by Gemini API if present."""
        m = re.search(
            r"(?:retry\s+(?:in|after)|wait|retry-after\D+)\s*([0-9]+(?:\.[0-9]+)?)\s*s?",
            err_str,
            re.IGNORECASE,
        )
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    async def generate_text(
        self,
        agent_name: str,
        prompt: str,
        event_bus: Optional[EventBus] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_retries: int = 3,
    ) -> str:
        """Generates plain text with on-disk cache, concurrency limit, fallback models, and backoff."""
        if not self.is_configured:
            raise RuntimeError(
                "GEMINI_API_KEY is not set or client initialization failed. "
                "Please configure GEMINI_API_KEY in .env"
            )

        # 1. Check on-disk prompt cache
        prompt_hash = self._compute_prompt_hash(prompt, system_instruction)
        cached_response = self._get_cached_response(prompt_hash)
        if cached_response is not None:
            return cached_response

        models_chain = self._get_models_chain()
        current_model_idx = 0

        while current_model_idx < len(models_chain):
            current_model = models_chain[current_model_idx]
            attempt = 0
            base_delay = 2.0

            while attempt < max_retries:
                attempt += 1
                try:
                    async with _GLOBAL_LLM_SEMAPHORE:
                        def _call_gemini():
                            config = types.GenerateContentConfig(
                                temperature=temperature,
                                system_instruction=system_instruction,
                            )
                            return self._client.models.generate_content(
                                model=current_model,
                                contents=prompt,
                                config=config,
                            )

                        response = await asyncio.to_thread(_call_gemini)
                        text = response.text or ""
                        cleaned = text.strip()

                        # Save to on-disk cache
                        self._set_cached_response(prompt_hash, cleaned, current_model)
                        self.model = current_model
                        return cleaned

                except Exception as e:
                    err_str = str(e)
                    is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str.upper()

                    if is_rate_limit:
                        api_delay = self._extract_retry_delay(err_str)
                        delay = api_delay if api_delay is not None else (base_delay * (2 ** (attempt - 1)))

                        if event_bus:
                            await event_bus.emit(
                                agent=agent_name,
                                event_type="recovery",
                                message=(
                                    f"Gemini rate limit (429) on model '{current_model}'. "
                                    f"Waiting {delay:.1f}s before next attempt/fallback..."
                                ),
                                details={"error": err_str, "attempt": attempt, "backoff": delay, "model": current_model},
                            )
                        await asyncio.sleep(min(delay, 60.0))

                        # If we have a fallback model available in the chain, switch to it!
                        if current_model_idx + 1 < len(models_chain):
                            next_model = models_chain[current_model_idx + 1]
                            current_model_idx += 1
                            if event_bus:
                                await event_bus.emit(
                                    agent=agent_name,
                                    event_type="recovery",
                                    message=(
                                        f"Switching Gemini model from '{current_model}' to fallback '{next_model}' "
                                        f"due to 429 rate limit."
                                    ),
                                    details={"previous_model": current_model, "new_model": next_model},
                                )
                            self.model = next_model
                            # Break inner retry loop to try the next model
                            break
                        else:
                            # No more fallback models, retry current model if attempts remain
                            if attempt < max_retries:
                                continue

                    if attempt < max_retries and not is_rate_limit:
                        delay = base_delay
                        if event_bus:
                            await event_bus.emit(
                                agent=agent_name,
                                event_type="recovery",
                                message=f"Gemini API error ({current_model}): {err_str[:120]}. Retrying in {delay}s...",
                                details={"error": err_str, "attempt": attempt, "model": current_model},
                            )
                        await asyncio.sleep(delay)
                        continue

                    # If not rate limit and out of retries for this model, try fallback model if any
                    if current_model_idx + 1 < len(models_chain):
                        next_model = models_chain[current_model_idx + 1]
                        current_model_idx += 1
                        if event_bus:
                            await event_bus.emit(
                                agent=agent_name,
                                event_type="recovery",
                                message=f"Switching model from '{current_model}' to fallback '{next_model}' after error: {err_str[:80]}",
                                details={"previous_model": current_model, "new_model": next_model},
                            )
                        self.model = next_model
                        break
                    else:
                        if event_bus:
                            await event_bus.emit(
                                agent=agent_name,
                                event_type="error",
                                message=f"Gemini API failed on all models and attempts: {err_str}",
                            )
                        raise

            # If inner loop finished without switching, break out
            if current_model_idx >= len(models_chain):
                break

        raise RuntimeError(f"Gemini API failed after trying all fallback models: {models_chain}.")

    async def generate_json(
        self,
        agent_name: str,
        prompt: str,
        event_bus: Optional[EventBus] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_retries: int = 3,
    ) -> Union[Dict[str, Any], List[Any]]:
        """
        Generates strict JSON with up to 3 retries, feeding back parse errors
        and handling Gemini 429 rate limit backoff.
        """
        current_prompt = prompt
        attempt = 0

        while attempt < max_retries:
            attempt += 1
            raw_text = ""
            try:
                raw_text = await self.generate_text(
                    agent_name=agent_name,
                    prompt=current_prompt,
                    event_bus=event_bus,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_retries=max_retries,
                )

                parsed = self._extract_and_parse_json(raw_text)
                return parsed

            except (json.JSONDecodeError, ValueError) as json_err:
                err_msg = str(json_err)
                if attempt < max_retries:
                    if event_bus:
                        await event_bus.emit(
                            agent=agent_name,
                            event_type="recovery",
                            message=(
                                f"LLM returned invalid JSON (attempt {attempt}/{max_retries}). "
                                f"Error: {err_msg}. Retrying with error feedback..."
                            ),
                            details={
                                "error": err_msg,
                                "raw_snippet": raw_text[:300],
                                "attempt": attempt,
                            },
                        )

                    # Feed back error into prompt
                    current_prompt = (
                        f"{prompt}\n\n"
                        f"CRITICAL FEEDBACK: Your previous response failed JSON parsing with error:\n"
                        f"'{err_msg}'\n"
                        f"Snippet of invalid output:\n```\n{raw_text[:400]}\n```\n"
                        f"Please fix this and return ONLY valid, well-formed JSON without markdown code fences or explanatory prose."
                    )
                    await asyncio.sleep(1.0)
                    continue

                if event_bus:
                    await event_bus.emit(
                        agent=agent_name,
                        event_type="error",
                        message=f"Failed to obtain valid JSON from LLM after {max_retries} attempts.",
                        details={"error": err_msg, "last_raw": raw_text[:300]},
                    )
                raise ValueError(
                    f"LLM returned invalid JSON after {max_retries} attempts: {err_msg}. Raw: {raw_text[:200]}"
                )

        raise ValueError(f"Failed to produce JSON after {max_retries} attempts.")

    @staticmethod
    def _extract_and_parse_json(text: str) -> Union[Dict[str, Any], List[Any]]:
        """Robustly extracts JSON from LLM text output."""
        text = text.strip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Check for ```json ... ``` or ``` ... ```
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try to locate the outermost [ ... ] or { ... }
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
