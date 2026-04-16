
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.engine.router_service import build_route_decision


@dataclass(frozen=True)
class RealWorldCase:
    input: str
    expected: str
    note: str
    expected_second_call: Optional[str] = None


TOP_20_PRIORITY_CASES = [
    RealWorldCase("Explique-moi simplement ce qu’est un embedding.", "explain_basic", "top20"),
    RealWorldCase("C’est quoi un LLM ?", "explain_basic", "top20"),
    RealWorldCase("Explique en détail le mécanisme d’attention.", "explain_advanced", "top20"),
    RealWorldCase("Écris-moi un script Python simple pour parser un CSV.", "build", "top20"),
    RealWorldCase("Corrige ce code et dis-moi ce qui ne va pas.", "critique", "top20"),
    RealWorldCase("Fais-moi un quiz sur les LLM.", "quiz", "top20"),
    RealWorldCase("Compare deux architectures de mémoire pour un assistant local.", "architecture", "top20"),
    RealWorldCase("Cherche les dernières avancées sur la fusion nucléaire.", "web_research", "top20"),
    RealWorldCase("Analyse cette image.", "vision", "top20"),
    RealWorldCase("Explique-moi les embeddings et donne-moi un exemple de code en Python.", "explain_basic", "top20", "build"),
    RealWorldCase("Compare deux architectures et propose une implémentation simple.", "architecture", "top20", "build"),
    RealWorldCase("Corrige ce code et propose une version améliorée.", "critique", "top20", "build"),
    RealWorldCase("explique moi vite fait les embeddings", "explain_basic", "top20"),
    RealWorldCase("t’as des sources sur les dernières avancées IA ?", "web_research", "top20"),
    RealWorldCase("explique moi internet simplement", "explain_basic", "top20"),
    RealWorldCase("C’est quoi une source dans un article scientifique ?", "explain_basic", "top20"),
    RealWorldCase("Explique-moi ce qu’est une image latente.", "explain_basic", "top20"),
    RealWorldCase("Je veux comprendre mon erreur dans ce script.", "critique", "top20"),
    RealWorldCase("Tu me proposes quoi pour stocker ça proprement ?", "architecture", "top20"),
    RealWorldCase("Critique mon router_service actuel.", "critique", "top20"),
]


def run_benchmark(cases, output_path):
    lines = []
    success = 0
    total = len(cases)

    for i, case in enumerate(cases, 1):
        decision = build_route_decision(case.input, has_image=False)

        predicted = decision.get("task_type")
        second_call = decision.get("second_call")

        status = "OK" if predicted == case.expected else "FAIL"
        if status == "OK":
            success += 1

        second_status = "N/A"
        if case.expected_second_call:
            second_status = "OK" if second_call == case.expected_second_call else "FAIL"

        lines.append(f"{i:03d} ===============================")
        lines.append(f"INPUT: {case.input}")
        lines.append(f"EXPECTED: {case.expected}")
        lines.append(f"PREDICTED: {predicted}")
        lines.append(f"STATUS: {status}")
        lines.append(f"EXPECTED_SECOND_CALL: {case.expected_second_call}")
        lines.append(f"PREDICTED_SECOND_CALL: {second_call}")
        lines.append(f"SECOND_CALL_STATUS: {second_status}")
        lines.append("")

    accuracy = round((success / total) * 100, 2)

    lines.append("===================================")
    lines.append(f"TOTAL: {total}")
    lines.append(f"SUCCESS: {success}")
    lines.append(f"ACCURACY: {accuracy}%")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")

    return output_path


if __name__ == "__main__":
    output = r"E:\AI_ASSISTANT_CORE\benchmarks\benchmark_top20_pipeline.txt"
    path = run_benchmark(TOP_20_PRIORITY_CASES, output)
    print(f"Benchmark generated at: {path}")
