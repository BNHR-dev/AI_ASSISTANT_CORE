from app.engine.task_routing import TASK_ROUTING


def select_model(task_type: str) -> tuple[str, str]:
    """
    Wrapper de compatibilité.
    La vraie source de vérité est désormais TASK_ROUTING.
    """
    route = TASK_ROUTING.get(task_type)

    if route is None:
        default_route = TASK_ROUTING["explain_basic"]
        return (
            default_route.model,
            "Task type inconnu : fallback vers explain_basic.",
        )

    return (
        route.model,
        f"Modèle récupéré depuis TASK_ROUTING pour la tâche {task_type}.",
    )
