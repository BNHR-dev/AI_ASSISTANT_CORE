# Contributing to AI_ASSISTANT_CORE

Thanks for your interest. A few things to know before opening a pull request.

## License of contributions

AI_ASSISTANT_CORE is licensed under [AGPL-3.0](LICENSE), and the maintainer keeps
the option to offer commercial licenses (dual-licensing). To preserve that, every
contribution must be covered by the [Contributor License Agreement](CLA.md).

By opening a pull request you agree to the terms in [`CLA.md`](CLA.md): you keep
the copyright on your work and grant the maintainer a broad license, including the
right to relicense. Contributions are accepted only on that basis.

## Sign your commits

Please sign your commits and sign them off so the authorship chain stays
verifiable:

```bash
git commit -S -s -m "your message"
```

## Ground rules

- Keep changes focused and reversible; do not break the `router → planner → executor` core.
- Run the test suite before opening a PR.
- Be honest in docs — no overselling.

## Running the tests

The suite is split into three tiers:

| tier | selection | needs | when |
|---|---|---|---|
| unit | default (`python -m pytest`) | nothing — fully hermetic | every commit, CI |
| integration | `-m integration` | a local Blender binary (self-skips when absent) | before a PR that touches the Blender pipeline |
| live | `AAC_LIVE_TESTS=1`, or `scripts/linux/live-tests.sh` | the running Docker stack + GPU | daily / before a release |

```bash
# unit + whatever integration your machine supports (what CI runs):
cd core && python -m pytest -q

# live tier against the running stack (Linux, resolves container IPs itself):
./scripts/linux/live-tests.sh
```

The live tier generates real images and Blender scenes, then **replays them
through the reproduce engine** and asserts the verdicts — it is the net that
hermetic tests cannot provide. Property-based fuzzing (Hypothesis) runs in the
unit tier: `core/tests/test_fuzz_properties.py`.
