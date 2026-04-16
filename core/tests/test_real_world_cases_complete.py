from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.engine.router_service import build_route_decision
from app.engine.executor import execute_request


@dataclass(frozen=True)
class RealWorldCase:
    input: str
    expected: str
    note: str
    expected_second_call: Optional[str] = None


# =========================================================
# 1. CORPUS COMPLET
# =========================================================
REAL_WORLD_CASES = [
    RealWorldCase(
        input="Explique-moi simplement ce qu’est un embedding.",
        expected="explain_basic",
        note="cas de base",
    ),
    RealWorldCase(
        input="C’est quoi un LLM ?",
        expected="explain_basic",
        note="naturel",
    ),
    RealWorldCase(
        input="Explique en détail le mécanisme d’attention.",
        expected="explain_advanced",
        note="advanced",
    ),
    RealWorldCase(
        input="Écris-moi un script Python simple pour parser un CSV.",
        expected="build",
        note="build",
    ),
    RealWorldCase(
        input="Corrige ce code et dis-moi ce qui ne va pas.",
        expected="critique",
        note="critique",
    ),
    RealWorldCase(
        input="Fais-moi un quiz sur les LLM.",
        expected="quiz",
        note="quiz",
    ),
    RealWorldCase(
        input="Compare deux architectures de mémoire.",
        expected="architecture",
        note="architecture",
    ),
    RealWorldCase(
        input="Cherche les dernières avancées sur la fusion nucléaire.",
        expected="web_research",
        note="web",
    ),
    RealWorldCase(
        input="Analyse cette image.",
        expected="vision",
        note="vision",
    ),
    RealWorldCase(
        input="Explique-moi les embeddings et donne-moi un exemple de code en Python.",
        expected="explain_basic",
        expected_second_call="build",
        note="hybride",
    ),
]


# =========================================================
# 2. SOUS-ENSEMBLE PRIORITAIRE
# =========================================================
TOP_20_PRIORITY_CASES = [
    RealWorldCase(
        input="Explique-moi simplement ce qu’est un embedding.",
        expected="explain_basic",
        note="top20",
    ),
    RealWorldCase(
        input="C’est quoi un LLM ?",
        expected="explain_basic",
        note="top20",
    ),
    RealWorldCase(
        input="Explique en détail le mécanisme d’attention.",
        expected="explain_advanced",
        note="top20",
    ),
    RealWorldCase(
        input="Écris-moi un script Python simple pour parser un CSV.",
        expected="build",
        note="top20",
    ),
    RealWorldCase(
        input="Corrige ce code et dis-moi ce qui ne va pas.",
        expected="critique",
        note="top20",
    ),
    RealWorldCase(
        input="Fais-moi un quiz sur les LLM.",
        expected="quiz",
        note="top20",
    ),
    RealWorldCase(
        input="Compare deux architectures de mémoire pour un assistant local.",
        expected="architecture",
        note="top20",
    ),
    RealWorldCase(
        input="Cherche les dernières avancées sur la fusion nucléaire.",
        expected="web_research",
        note="top20",
    ),
    RealWorldCase(
        input="Analyse cette image.",
        expected="vision",
        note="top20",
    ),
    RealWorldCase(
        input="Explique-moi les embeddings et donne-moi un exemple de code en Python.",
        expected="explain_basic",
        expected_second_call="build",
        note="top20",
    ),
    RealWorldCase(
        input="Compare deux architectures et propose une implémentation simple.",
        expected="architecture",
        expected_second_call="build",
        note="top20",
    ),
    RealWorldCase(
        input="Corrige ce code et propose une version améliorée.",
        expected="critique",
        expected_second_call="build",
        note="top20",
    ),
    RealWorldCase(
        input="explique moi vite fait les embeddings",
        expected="explain_basic",
        note="top20",
    ),
    RealWorldCase(
        input="t’as des sources sur les dernières avancées IA ?",
        expected="web_research",
        note="top20",
    ),
    RealWorldCase(
        input="explique moi internet simplement",
        expected="explain_basic",
        note="top20",
    ),
    RealWorldCase(
        input="C’est quoi une source dans un article scientifique ?",
        expected="explain_basic",
        note="top20",
    ),
    RealWorldCase(
        input="Explique-moi ce qu’est une image latente.",
        expected="explain_basic",
        note="top20",
    ),
    RealWorldCase(
        input="Je veux comprendre mon erreur dans ce script.",
        expected="critique",
        note="top20",
    ),
    RealWorldCase(
        input="Tu me proposes quoi pour stocker ça proprement ?",
        expected="architecture",
        note="top20",
    ),
    RealWorldCase(
        input="Critique mon router_service actuel.",
        expected="critique",
        note="top20",
    ),
]


# =========================================================
# 3. FORMATAGE TEXTE
# =========================================================
def format_cases(cases: list[RealWorldCase], title: str) -> str:
    lines = [f"=== {title} ===", ""]

    for i, case in enumerate(cases, 1):
        lines.append(f"{i:03d}. {case.input}")
        lines.append(f"     expected={case.expected}")
        if case.expected_second_call is not None:
            lines.append(f"     expected_second_call={case.expected_second_call}")
        lines.append(f"     note={case.note}")
        lines.append("")

    return "\n".join(lines)


# =========================================================
# 4. EXPORT SIMPLE DES INPUTS (optionnel)
# =========================================================
def export_cases_txt(
    full_cases: list[RealWorldCase],
    top_cases: list[RealWorldCase],
    output_dir: str = r"E:\AI_ASSISTANT_CORE\benchmarks",
    filename: str = "benchmark_classifier_all_cases.txt",
) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / filename

    content = "\n\n".join([
        format_cases(top_cases, "TOP CASES"),
        format_cases(full_cases, "ALL CASES"),
    ])

    file_path.write_text(content, encoding="utf-8")
    return file_path


# =========================================================
# 5. VRAI BENCHMARK PIPELINE
# =========================================================
def run_full_pipeline_benchmark(
    cases: list[RealWorldCase],
    output_dir: str = r"E:\AI_ASSISTANT_CORE\benchmarks",
    filename: str = "benchmark_classifier_pipeline.txt",
    run_executor: bool = False,
) -> Path:
    """
    Passe chaque input dans la pipeline réelle :
    build_route_decision() puis éventuellement execute_request()

    run_executor=False conseillé au début :
    - plus rapide
    - évite d'appeler les LLM
    - permet de tester juste le routing

    run_executor=True :
    - exécute aussi la génération finale
    """

    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / filename

    lines: list[str] = []
    total = len(cases)
    success_task = 0
    success_second_call = 0
    second_call_total = 0

    for i, case in enumerate(cases, 1):
        try:
            decision = build_route_decision(case.input, has_image=False)

            predicted_task = decision.get("task_type")
            predicted_second_call = decision.get("second_call")
            selected_tool = decision.get("selected_tool")
            decision_trace = decision.get("decision_trace", [])
            reason = decision.get("reason", "")

            task_status = "OK" if predicted_task == case.expected else "FAIL"
            if task_status == "OK":
                success_task += 1

            second_call_status = "N/A"
            if case.expected_second_call is not None:
                second_call_total += 1
                second_call_status = (
                    "OK" if predicted_second_call == case.expected_second_call else "FAIL"
                )
                if second_call_status == "OK":
                    success_second_call += 1

            output_text = ""
            if run_executor:
                result = execute_request(case.input, has_image=False)
                output_text = result.get("output", "")[:500]

            lines.append(f"{i:03d} ===============================")
            lines.append(f"INPUT: {case.input}")
            lines.append(f"NOTE: {case.note}")
            lines.append("")
            lines.append(f"EXPECTED_TASK: {case.expected}")
            lines.append(f"PREDICTED_TASK: {predicted_task}")
            lines.append(f"TASK_STATUS: {task_status}")
            lines.append("")
            lines.append(f"EXPECTED_SECOND_CALL: {case.expected_second_call}")
            lines.append(f"PREDICTED_SECOND_CALL: {predicted_second_call}")
            lines.append(f"SECOND_CALL_STATUS: {second_call_status}")
            lines.append("")
            lines.append(f"SELECTED_TOOL: {selected_tool}")
            lines.append(f"REASON: {reason}")
            lines.append("DECISION_TRACE:")
            if decision_trace:
                for step in decision_trace:
                    lines.append(f" - {step}")
            else:
                lines.append(" - None")

            if run_executor:
                lines.append("")
                lines.append("OUTPUT:")
                lines.append(output_text if output_text else "[EMPTY OUTPUT]")

            lines.append("")

        except Exception as e:
            lines.append(f"{i:03d} ERROR ===============================")
            lines.append(f"INPUT: {case.input}")
            lines.append(f"NOTE: {case.note}")
            lines.append(f"ERROR: {str(e)}")
            lines.append("")

    task_accuracy = round((success_task / total) * 100, 2) if total else 0.0
    if second_call_total > 0:
        second_call_accuracy = round((success_second_call / second_call_total) * 100, 2)
    else:
        second_call_accuracy = 0.0

    lines.append("===================================")
    lines.append("SUMMARY")
    lines.append(f"TOTAL_CASES: {total}")
    lines.append(f"TASK_SUCCESS: {success_task}")
    lines.append(f"TASK_ACCURACY: {task_accuracy}%")
    lines.append(f"SECOND_CALL_CASES: {second_call_total}")
    lines.append(f"SECOND_CALL_SUCCESS: {success_second_call}")
    lines.append(f"SECOND_CALL_ACCURACY: {second_call_accuracy}%")

    file_path.write_text("\n".join(lines), encoding="utf-8")
    return file_path


# =========================================================
# 6. MAIN
# =========================================================
if __name__ == "__main__":
    print("=== EXPORT DES CAS ===\n")
    print(format_cases(TOP_20_PRIORITY_CASES, "TOP CASES"))
    print(format_cases(REAL_WORLD_CASES, "ALL CASES"))

    # Export simple des inputs
    cases_file = export_cases_txt(
        full_cases=REAL_WORLD_CASES,
        top_cases=TOP_20_PRIORITY_CASES,
        output_dir=r"E:\AI_ASSISTANT_CORE\benchmarks",
        filename="benchmark_classifier_all_cases.txt",
    )
    print(f"\n✅ Cas exportés dans : {cases_file}")

    # Vrai benchmark pipeline
    benchmark_file = run_full_pipeline_benchmark(
        cases=REAL_WORLD_CASES,
        output_dir=r"E:\AI_ASSISTANT_CORE\benchmarks",
        filename="benchmark_classifier_pipeline.txt",
        run_executor=False,  # mets True plus tard si tu veux aussi la génération finale
    )
    print(f"✅ Benchmark pipeline exporté dans : {benchmark_file}")