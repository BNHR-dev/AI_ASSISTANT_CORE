from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class TaskRoute:
    task_type: str
    primary_agent: str
    model: str
    web: bool
    second_call: Optional[str]
    output_format: str


TASK_ROUTING: Dict[str, TaskRoute] = {
    "explain_basic": TaskRoute(
        task_type="explain_basic",
        primary_agent="AGENT_PROF_IA",
        model="qwen3:8b",
        web=False,
        second_call=None,
        output_format="definition + mental_model + concrete_example",
    ),
    "explain_advanced": TaskRoute(
        task_type="explain_advanced",
        primary_agent="AGENT_PROF_IA",
        model="qwen3:8b",
        web=False,
        second_call=None,
        output_format="detailed_explanation + concepts + implications",
    ),
    "architecture": TaskRoute(
        task_type="architecture",
        primary_agent="AGENT_ARCHI_IA",
        model="qwen3:8b",
        web=False,
        second_call=None,
        output_format="options + comparison + decision + system_impacts",
    ),
    "build": TaskRoute(
        task_type="build",
        primary_agent="AGENT_BUILDER_IA",
        model="qwen2.5-coder:7b",
        web=False,
        second_call=None,
        output_format="python_module + structure + test_instructions + usage",
    ),
    "quiz": TaskRoute(
        task_type="quiz",
        primary_agent="AGENT_EXAM_IA",
        model="qwen3:8b",
        web=False,
        second_call=None,
        output_format="progressive_questions + correction + feedback",
    ),
    "critique": TaskRoute(
        task_type="critique",
        primary_agent="AGENT_EXAM_IA",
        model="qwen3:8b",
        web=False,
        second_call=None,
        output_format="analysis + errors + improvements + justification",
    ),
    "web_research": TaskRoute(
        task_type="web_research",
        primary_agent="AGENT_PROF_IA",
        model="qwen3:8b",
        web=True,
        second_call=None,
        output_format="synthesis + useful_sources + clear_summary",
    ),
    "vision": TaskRoute(
        task_type="vision",
        primary_agent="AGENT_VISION_IA",
        model="qwen2.5vl:3b",
        web=False,
        second_call=None,
        output_format="description + analysis + visual_interpretation",
    ),
    "image_generation": TaskRoute(
        task_type="image_generation",
        primary_agent="AGENT_CREATIVE_IA",
        model="qwen3:8b",
        web=False,
        second_call=None,
        output_format="structured_prompt + visual_parameters",
    ),
}