"""Question generation and the procedural solvability gate."""

from .gate import validate_question
from .pipeline import (
    GenerationAttempt,
    GenerationResult,
    build_user_prompt,
    generate_question,
)

__all__ = [
    "GenerationAttempt",
    "GenerationResult",
    "build_user_prompt",
    "generate_question",
    "validate_question",
]
