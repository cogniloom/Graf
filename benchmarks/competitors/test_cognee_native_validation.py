"""Exercise the pinned native correction loop without inference/network calls."""
import importlib.util
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(importlib.util.find_spec("cognee"), "Run with the pinned Cognee interpreter")
class NativeValidationTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, responses):
        from cognee.infrastructure.llm.structured_output_framework.litellm_native import native_adapter
        from pydantic import BaseModel

        class Summary(BaseModel):
            summary: str

        completion = AsyncMock(side_effect=responses)
        instance = native_adapter.NativeLiteLLMAdapter.__new__(native_adapter.NativeLiteLLMAdapter)
        with (patch.object(native_adapter, "_MAX_VALIDATION_RETRIES", 3),
              patch.object(native_adapter, "llm_rate_limiter_context_manager", return_value=nullcontext()),
              patch.object(native_adapter.litellm, "acompletion", completion)):
            try:
                result = await instance._acreate_json_fallback(
                    "source", "summarize", Summary, model="openai/gpt-6-luna",
                    api_key=None, endpoint=None, api_version=None)
                return result, completion
            except TimeoutError:
                self.assertEqual(completion.await_count, 1)
                raise

    async def test_completed_invalid_json_receives_native_feedback(self):
        def response(content):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        result, completion = await self.exercise([response("not JSON"), response('{"summary":"valid"}')])
        self.assertEqual(result.summary, "valid")
        self.assertEqual(completion.await_count, 2)
        self.assertIn("previous response failed validation", completion.await_args.kwargs["messages"][1]["content"])

    async def test_transport_timeout_is_not_a_validation_retry(self):
        with self.assertRaises(TimeoutError):
            await self.exercise([TimeoutError("unknown transport outcome")])
