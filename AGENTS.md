# Repository Agent Instructions

These instructions are the thin control layer for repository work. They supplement, rather than replace, the current user request, source code, tests, README, and project documentation.

## Task start

1. Understand the current user request and preserve the existing task scope.
2. Inspect the current Git branch/status, recent commits, relevant code, tests, CI, and uncommitted changes. Never reset, discard, or overwrite unrelated work.
3. Read `.ai/index.md`.
4. Extract the domain, files, symbols, errors, protocols, and other high-signal keywords from the task.
5. Search `.ai/` with those terms, for example `rg -n -i 'term1|term2' .ai`.
6. Rank matches and read only the entries that are plausibly relevant.
7. Validate retrieved knowledge against the current implementation and current evidence before using it.

Do not `cat` or otherwise load all of `.ai/` by default. Do not turn this file into a knowledge dump.

## Authority order

When sources conflict, prefer:

1. current code;
2. current test results;
3. a current reproduction or experiment;
4. official specifications or other primary sources;
5. active repository Decisions;
6. Validated Rules;
7. Candidate Rules;
8. Observations and Case Memory.

A historical entry is evidence, not truth. Re-check it when the implementation, dependency, chain state, data source, or operating assumptions may have changed.

## During work

- Keep the user's primary deliverable ahead of knowledge maintenance.
- Preserve the repository's research, leakage, economic, and execution-safety invariants unless the user explicitly changes the product requirements and the change is validated.
- Prefer evidence-bearing changes and tests over narrative assertions.
- Never weaken a fail-closed boundary merely to make a test, campaign, or gate pass.

## Task end

For substantial work, ask whether anything learned would change a future agent's decision, prevent repeated investigation, prevent a meaningful failure, or preserve non-obvious rationale.

If no, do not store it. If yes:

1. search `.ai/` for equivalent knowledge first;
2. update/strengthen an existing entry when possible instead of creating a duplicate;
3. attach evidence, applicability, limitations, and confidence;
4. record a Case for issue-specific work, an Observation for a potentially reusable finding, a Failure for a costly repeatable dead end, or a Decision for important rationale;
5. promote knowledge only through `Case -> Observation -> Candidate Rule -> Validated Rule` when the evidence warrants it;
6. update `.ai/index.md` and the relevant directory index.

Do not save trivial logs, source-code copies, README copies, easily regenerated facts, command typos, or unsupported speculation.

## Security

Never store API keys, private keys, seed phrases, passwords, bearer tokens, credentials, `.env` secret values, or unnecessary personal information in `.ai/`. Record only the abstract requirement or redacted evidence needed for future decisions.

## External Intelligence maintenance

The storage model, templates, confidence levels, promotion rules, deduplication rules, and retrieval workflow are defined in `.ai/README.md`. Markdown + Git + filesystem search is the current implementation; do not add databases, embeddings, vector search, or a knowledge graph until measured retrieval needs justify them.
