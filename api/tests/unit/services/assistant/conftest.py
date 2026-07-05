import json

import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel


@pytest.fixture(autouse=True)
def _block_real_models():
    """Hard-block accidental real LLM calls in the assistant test suite."""
    previous = models.ALLOW_MODEL_REQUESTS
    models.ALLOW_MODEL_REQUESTS = False
    yield
    models.ALLOW_MODEL_REQUESTS = previous


def text_step(content: str) -> tuple:
    return ("text", content)


def tool_step(name: str, args: dict) -> tuple:
    return ("tool", name, args)


def scripted_model(steps: list[tuple]) -> FunctionModel:
    """A FunctionModel that replays scripted steps, one per model request.

    Supports both non-streaming (``function``) and streaming (``stream_function``)
    runs; each step is ``text_step(...)`` or ``tool_step(name, args)``.
    """
    non_stream = iter(steps)
    streamed = iter(steps)

    def function(messages, info) -> ModelResponse:
        step = next(non_stream)
        if step[0] == "text":
            return ModelResponse(parts=[TextPart(step[1])])
        return ModelResponse(parts=[ToolCallPart(step[1], step[2])])

    async def stream_function(messages, info):
        step = next(streamed)
        if step[0] == "text":
            for word in step[1].split(" "):
                yield word + " "
        else:
            yield {0: DeltaToolCall(name=step[1], json_args=json.dumps(step[2]))}

    return FunctionModel(function, stream_function=stream_function)


def parse_sse(chunks: list[str]) -> list[dict]:
    """Parse collected SSE text into decoded frame dicts."""
    frames: list[dict] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                frames.append(json.loads(line[len("data: ") :]))
    return frames
