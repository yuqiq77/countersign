# Evaluation Suites

Evaluation data is separated by what it tests:

- `semantic/` contains human-reviewed cases for model judgment, blockers, evidence coverage, and prohibited decisions;
- `harness/` contains replay and regression reporting code;
- deterministic source/status fixtures live under `tests/fixtures/sources/`;
- prompt-injection and malformed-input cases live under `tests/security/`.

The initial semantic regression suite targets 20 cases and grows to 50 before the project claims “50 golden evals.” Public fixtures use minimal excerpts or structure-preserving rewrites; raw job postings remain private.

