"""Fabrique d'`ExecutionState` — PAS un magasin persistant.

L'état d'une exécution vit en mémoire le temps d'une requête ; un crash ou un
redémarrage le perd (seuls les artefacts déjà écrits sur disque survivent).
Un vrai store persistant (runs consultables, reprise) = roadmap, pas ce module.
Ex-`state_store.py`, renommé pour dire ce qu'il fait vraiment.
"""
from __future__ import annotations

from app.engine.planner_types import ExecutionPlan, ExecutionState


def create_execution_state(message: str, decision: dict, plan: ExecutionPlan) -> ExecutionState:
    state = ExecutionState(message=message, decision=decision, plan=plan)
    state.trace.extend(decision.get("decision_trace", []))
    state.add_trace(f"planner → strategy={plan.strategy}")
    for step in plan.steps:
        state.add_trace(f"planner → {step.step_id}:{step.step_type}")
    return state
