# Codex External Intelligence

This directory is a selective, evidence-first external memory for future repository agents. Its purpose is to reduce repeated investigation and improve decisions without loading a large knowledge base into every context.

## Operating model

```text
current task
  -> current code/Git state
  -> .ai/index.md
  -> keyword/file/symbol search
  -> read only relevant entries
  -> revalidate against current reality
  -> investigate / implement / test / verify
  -> save only future decision-changing knowledge
```

The lifecycle for generalized knowledge is:

```text
Case Memory -> Observation -> Candidate Rule -> Validated Rule
```

Promotion is not automatic. Repetition, independent evidence, tests, or primary-source confirmation are required.

## Taxonomy

- `cases/`: issue-, bug-, feature-, refactor-, or investigation-specific memory.
- `observations/`: potentially reusable findings that are not yet general rules.
- `failures/`: costly or plausible approaches worth remembering not to repeat.
- `decisions/`: important architectural, API, dependency, security, compatibility, or performance rationale.
- `rules/`: candidate, validated, and rejected/superseded decision rules.
- `intelligence/`: high-density repository-specific knowledge that is reusable across cases.

Raw logs belong in normal artifacts/evidence locations, not here.

## Retrieval and context efficiency

Start with `.ai/index.md`; do not recursively read every entry. Use local search first:

```bash
rg -n -i 'archive|rpc|historical' .ai
rg -n -i 'execution_intent|reorg|fork' .ai
rg -n -i 'campaign|oos|latency|profit' .ai
find .ai -type f -maxdepth 4
```

Search terms should normally come from the task's files, symbols, errors, domain terminology, protocols, or failing tests. Read the smallest set of high-relevance entries, then inspect the current source and reproduce or test as needed.

## Authority and staleness

External Intelligence is subordinate to current reality. Resolve conflicts using this order:

1. current code;
2. current test result;
3. current reproduction/experiment;
4. official specification/primary information;
5. active Decision;
6. Validated Rule;
7. Candidate Rule;
8. Observation/Case.

If an old entry is wrong, prefer `corrected`, `superseded`, or `rejected` with a link to the replacement over silent deletion when the correction itself can prevent repeated bad reasoning.

## Quality gate before writing

Store knowledge only when at least one is true:

- it can avoid repeating a meaningful investigation;
- it can prevent a repeatable failure;
- it would change a future agent's decision;
- it preserves rationale that is not obvious from code;
- it carries reusable evidence;
- it records an important design/security/performance tradeoff.

Do not store trivial information, temporary logs, work diaries, source/README copies, tool output dumps, duplicates, unsupported guesses, or facts that a quick code read regenerates reliably.

## Deduplication

Before creating an entry, search the index and full `.ai/` tree by the core concepts and synonyms. If an equivalent entry exists, update it with new evidence, confidence, applicability, exceptions, and related cases. Create a new entry only when the decision surface is materially different.

## Confidence

- `low`: hypothesis or single weak observation without adequate reproduction.
- `medium`: reproduced or supported by multiple pieces of evidence, but generalization remains uncertain.
- `high`: independently supported by implementation/tests/specification or multiple cases, with applicability and exceptions understood.

Confidence describes evidence quality, not writing certainty. Do not infer `high` from intuition.

## Evidence rules

Prefer compact, verifiable references:

- repository path + symbol/section;
- exact test name or command and result summary;
- commit/PR/issue identifier;
- persisted evidence artifact and the relevant fields;
- primary specification reference when external behavior matters.

Do not copy full command output. If an assertion can change over time, record the observation date and what must be rechecked.

## Templates

### Case Memory

A substantial case may use `cases/YYYY-MM-DD-short-description/{summary,evidence,attempts,outcome}.md`; a small case may be one file.

`summary.md` should capture problem, context, root cause if known, final solution/status, related files/tests, and commit/PR/issue references. `evidence.md` captures reproductions, commands/results summaries, code locations, and test evidence. `attempts.md` captures meaningful hypotheses and failed approaches plus why they failed. `outcome.md` captures final change, verification, remaining risk, follow-up, and reusable learning.

### Observation

```text
# Title
Status: observation
Date: YYYY-MM-DD
Source case: path or none
Confidence: low | medium | high

## Context
## Observation
## Evidence
## Why it matters
## Applicability
## Exceptions / Limitations
## Related files
## Related cases
```

One observation is not automatically a rule.

### Failure Memory

```text
# Failure title

## Context
## Attempt
## Why it seemed plausible
## Why it failed
## Evidence
## Better approach
## Applicability
```

### Decision Memory

```text
# Decision title
Status: active | superseded | rejected
Date: YYYY-MM-DD

## Context
## Decision
## Alternatives considered
## Evidence / Rationale
## Tradeoffs
## Consequences
## Revisit when
## Related code
```

Superseded decisions remain and link to their replacement when useful.

### Rule

Rules must be operational, not historical anecdotes. A useful rule says what to do, when it applies, how to verify it, and what exceptions exist.

```text
# Rule title
Status: candidate | validated | rejected | superseded
Confidence: low | medium | high

## Rule
## Applicability
## Verification
## Evidence
## Exceptions / Limitations
## Related cases / observations
```

## Promotion

Promote Observation -> Candidate only after independent repetition, a separate issue/module reproduction, or strong extra evidence such as tests or a primary specification. Promote Candidate -> Validated only when the behavior is reproducible, evidence is clear, applicability and known exceptions are defined, and the rule has practical future value.

Rejected and superseded rules stay in `rules/rejected.md` when remembering the bad generalization can prevent future error.

## Security

Never store secrets or credentials. A gate may say that a credential is required and may record redacted readiness booleans, but never its value, URL containing a secret, token, seed, private key, password, or raw `.env` data.

## Git and future evolution

`.ai/` is intended to be Git-tracked so knowledge evolution is reviewable. The current implementation deliberately uses Markdown, Git history, filenames, and `rg`/`grep`. The layout leaves room for future SQLite/FTS5, embeddings, hybrid retrieval, ranking, or knowledge graphs, but those are out of scope until retrieval quality/scale is measured and justifies them.
