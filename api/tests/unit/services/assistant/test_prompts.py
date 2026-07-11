from api.services.assistant.prompts import SYSTEM_PROMPT


def test_system_prompt_instructs_to_ask_clarifying_questions():
    assert "ask a short, specific clarifying question" in SYSTEM_PROMPT
    assert "guessing and running SQL" in SYSTEM_PROMPT
