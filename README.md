# Database Agent (MWDBA)

A **minimum working Database Agent**: it reads an `entities.json` file and progressively turns it into a
complete, tested PostgreSQL database — architecture, tables, constraints, indexes, CRUD functions and BDD tests —
recovering automatically from LLM-generated SQL errors.

> **The LLM proposes. The system validates. The database executes.**
> The LLM does reasoning, design, SQL generation, correction and test writing. Deterministic Python does
> validation, dependency checking, execution, transactions, retry limits, artifact persistence, logging,
> test orchestration and security checks.

## 1. What it does

```
Entity JSON ──► Architecture ──► Schema (DDL) ──► CRUD functions ──► BDD tests ──► Pass/Fail report
                 (validated)      (executed)        (executed)        (executed)
```

It is an engineering prototype, not a SaaS: a command-line tool for a **dedicated development database**.
The Frontend Agent that will eventually produce `entities.json` is **not** implemented here.

## 2. Architecture

```
Entity JSON
    ↓
Architecture Agent   LLM → Architecture JSON → deterministic validator + dependency graph
    ↓
Schema Agent         LLM → DDL statements → safety check → structure check
    ↓
PostgreSQL           execute in ONE transaction → introspect catalog and compare with architecture
    ↓
CRUD Agent           LLM → functions/procedures → safety check
    ↓
PostgreSQL           execute in ONE transaction → verify every declared function exists
    ↓
BDD Test Agent       LLM → Gherkin-style scenarios with executable SQL
    ↓
Test Execution       each scenario in a rolled-back transaction → report
    ↓                failures → LLM triage (schema | crud | test) → targeted repair → re-run
Pass / Fail report
```

| Component | Responsibility |
|---|---|
| `app/llm/base.py`, `provider.py` | `LLMProvider` ABC (Strategy) + `AnthropicProvider` + factory |
| `app/llm/prompt_loader.py` | Loads prompt **files**, strict `{{VARIABLE}}` substitution, `{{include:...}}` fragments |
| `app/llm/client.py` | Prompt + provider + Pydantic validation, bounded JSON-repair loop |
| `app/models/` | Pydantic contracts: entity, architecture, SQL scripts, CRUD, BDD tests |
| `app/database/validator.py` | Architecture validation (FKs, keys, identifiers, 1NF/3NF heuristics, relationships) |
| `app/database/dependency_graph.py` | Topological order, cycle detection (Tarjan), deferred-FK strategy |
| `app/database/sql_safety.py` | Statement allow-lists + forbidden-command scanner |
| `app/database/schema_checker.py` | DDL ↔ architecture structure and FK ordering check *before* execution |
| `app/database/executor.py` | All-or-nothing script execution, errors returned as data |
| `app/database/introspection.py` | Catalog snapshot (for prompts) and post-execution verification |
| `app/database/schema_manager.py` | The only code allowed to create/reset the agent-owned schema |
| `app/database/bdd_runner.py` | Deterministic scenario execution |
| `app/pipeline/correction.py` | Generic bounded generate → check → correct loop |
| `app/pipeline/*_stage.py` | The four stages (Template Method on `Stage`) |
| `app/pipeline/orchestrator.py` | Runs stages over a `PipelineContext`, saves attempt log |
| `app/composition.py` | Dependency-injection wiring |

## 3. Installation

Requires Python 3.12+ and Docker (or any PostgreSQL 14+).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set ANTHROPIC_API_KEY
```

## 4. PostgreSQL setup

```bash
docker compose up -d        # PostgreSQL 16, database "database_agent", restricted role "agent"
```

`docker/initdb/01-agent-role.sh` creates a **non-superuser** role (`NOCREATEDB NOCREATEROLE`) that owns only the
dedicated `database_agent` database. Default passwords are local-development placeholders; override them with
`POSTGRES_ADMIN_PASSWORD` / `AGENT_DB_PASSWORD` and keep `DATABASE_URL` in sync. **Never point `DATABASE_URL`
at a production database.**

All generated objects live in **one agent-owned PostgreSQL schema** (default: the project name). The connection pins
`search_path` to it, so the LLM only ever writes unqualified names.

## 5. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | Provider strategy (only `anthropic` is implemented) |
| `LLM_MODEL` | `claude-sonnet-5-5` | Model id |
| `ANTHROPIC_API_KEY` | – | Required for the Anthropic provider |
| `LLM_MAX_TOKENS` / `LLM_TIMEOUT_SECONDS` | `16000` / `300` | Per request |
| `DATABASE_URL` | local docker URL | Dev database connection string |
| `TARGET_SCHEMA` | project name | Agent-owned PostgreSQL schema |
| `STATEMENT_TIMEOUT_MS` | `30000` | Per-statement timeout for all generated SQL |
| `MAX_RETRIES` | `3` | **Total attempts** per stage (1 initial + `MAX_RETRIES-1` corrections) |
| `MAX_TEST_REPAIR_ROUNDS` | `2` | Triage/repair rounds after failing BDD tests |
| `GENERATED_DIR` / `LOGS_DIR` | `generated` / `logs` | Output locations |

Secrets (`ANTHROPIC_API_KEY`, the DB password) are held as `SecretStr` and scrubbed from logs and console output.

## 6. Entity JSON format (the Frontend Agent contract)

`requirements/entities.json` is the **only** high-level input. Validated by Pydantic (`app/models/entity.py`) before
the pipeline starts; extra keys are rejected.

```json
{
  "project": "example_shop",
  "entities": [
    {
      "name": "Order",
      "description": "A customer order",
      "fields": [
        {"name": "id", "type": "integer", "description": "Unique identifier"},
        {"name": "user_id", "type": "integer", "required": true,
         "references": {"entity": "User", "field": "id"}},
        {"name": "created_at", "type": "datetime"}
      ]
    }
  ],
  "relationships": [
    {"from_entity": "User", "to_entity": "Order", "cardinality": "one_to_many"}
  ]
}
```

* `project` – identifier (`[A-Za-z][A-Za-z0-9_]*`); also the default schema name.
* field `type` – `integer | string | text | decimal | boolean | datetime | date | uuid`.
* optional field hints – `required`, `unique`, `references {entity, field}` (must resolve).
* optional `relationships` – `one_to_one | one_to_many | many_to_many` between entities.
* entity/field names must be unique. See the full example in `requirements/entities.json`.

## 7. Pipeline stages

1. **Architecture** – the LLM returns tables, typed columns, PKs, FKs (with `ON DELETE/UPDATE`), uniques, justified
   indexes, relationships, normalization analysis. Code validates names (snake_case, ≤63, not reserved), duplicate
   tables/columns/FKs, FK target existence, **type compatibility** and PK/UNIQUE targets, relationship resolution,
   `SET NULL` on nullable columns only, 1NF (arrays, repeating groups) and entity coverage; transitive-copy columns are
   3NF *warnings*. The dependency graph rejects cycles unless a FK is marked `deferred` (added via `ALTER TABLE` after
   creation). `creation_order` and `dependencies` are **recomputed deterministically**.
2. **Schema** – the agent-owned schema is (re)created, the LLM writes DDL statements, code checks safety and that
   created tables equal the architecture and inline `REFERENCES` point at earlier tables; the script runs in one
   transaction and the **resulting catalog is compared with the architecture** (tables, columns, PKs, FKs) before commit.
3. **CRUD** – the LLM writes functions/procedures considering each table's role (junction/line tables get relationship
   operations; skipped tables must be justified). Executed in one transaction; every declared function must exist.
4. **Tests** – BDD scenarios (happy path, constraint violations, relationships, transactions, boundaries, lifecycle) with
   executable SQL are generated, statically checked, executed, and reported.

## 8. Prompt architecture

**Every LLM operation has a prompt file; Python contains no prompt text.**

```
app/prompts/
├── architecture/generate_architecture.md
├── schema/generate_schema.md
├── crud/generate_crud.md
├── testing/generate_bdd_tests.md
├── correction/{architecture,schema,crud,test}_correction.md
├── correction/test_failure_triage.md
├── correction/structured_output_correction.md
└── shared/{postgresql,normalization,acid,security,json_output}_rules.md   # included fragments
```

Each prompt has the ten dimensions (Role, Objective, Input, Context, Constraints, Database rules, Reasoning
considerations, Output format, Validation requirements, Failure/correction expectations) — enforced by
`tests/unit/test_prompts.py`. Templates use `{{UPPER_CASE}}` variables (strict: missing/unused variables are errors) and
`{{include:shared/file.md}}`. The output contract in each prompt is the **JSON Schema generated from the Pydantic model**
(`{{OUTPUT_JSON_SCHEMA}}`), so prompt and validator cannot drift. Each file starts with `<!-- prompt-id: ... -->`, which
makes logs readable and lets tests script an LLM.

## 9. LLM configuration

`LLMProvider.generate(prompt) -> str` is the whole contract. Add a provider by implementing it and registering it in
`create_provider()` (`app/llm/provider.py`); stages never import a vendor SDK. Only the Anthropic provider exists.
Credentials come from `ANTHROPIC_API_KEY` (an `ant auth login` profile is not used by the pre-flight check).

## 10. Running

```bash
python -m app.main generate requirements/entities.json
# optional: --max-retries 5 --schema my_schema
```

Exit codes: `0` success and all tests pass · `1` a stage failed after its attempts · `2` invalid input/config/cannot start ·
`3` pipeline completed but some BDD tests still fail after repair rounds.

## 11. Generated artifacts

```
generated/
├── architecture.json     validated architecture (with recomputed creation_order)
├── schema.sql            executed DDL
├── crud.sql              executed functions
├── database_state.json   catalog snapshot (tables, constraints, indexes, functions)
├── tests/*.feature       Gherkin per feature + tests/suite.json (executable form)
├── test-report.json|txt  results
└── attempts.json         every attempt of every stage (artifact + error)
logs/run-<timestamp>.jsonl   stage, timestamp, LLM request/response, validation, SQL result, error, attempt, status
```

## 12. Error correction loop

```
Generated SQL → PostgreSQL → ERROR → Correction prompt (requirements + architecture + failed SQL + PostgreSQL error/SQLSTATE
+ live database state + previous attempts) → corrected SQL → PostgreSQL → ...
```

* Bounded: `MAX_RETRIES` total attempts (default 3 = initial + 2 corrections), then the stage fails with exit code 1.
* Malformed/invalid JSON from the LLM goes through its own bounded repair prompt before it ever reaches a stage.
* Architecture failures use validator messages; schema/CRUD failures use static-check or PostgreSQL errors; execution is
  transactional, so a failed attempt leaves nothing behind.
* **Test failures** are triaged by the LLM into `schema`, `crud` or `test` defects. Only that layer is repaired (test defects:
  the scenario is rewritten; CRUD: functions corrected and re-executed; schema: schema reset + corrected + CRUD re-applied), then tests re-run,
  up to `MAX_TEST_REPAIR_ROUNDS`. Triage is ambiguous → prefer fixing the test, not the database.

## 13. Testing

```bash
pip install -r requirements.txt
pytest tests/unit                                   # no database needed
docker compose up -d
export TEST_DATABASE_URL=postgresql://agent:agent_dev_only@localhost:5432/database_agent
pytest                                              # + integration tests against real PostgreSQL
```

Integration tests run the **entire pipeline against real PostgreSQL with a scripted LLM** (`tests/fakes/`): wrong first
architecture, wrong first DDL (real PostgreSQL error), malformed JSON, a defective test, a CRUD defect found by the tests,
exhausted retries, and refusal to touch a foreign schema. They need no API key and no network.

BDD scenarios run each in one transaction that is **always rolled back**, with a savepoint per step. This is deterministic and
isolated but cannot observe real commits across connections; "transaction" tests verify atomicity of a single function call.

## 14. Security and limitations

* Run only against a **dedicated development database** with the restricted role from `docker/`.
* The LLM can never reset or drop anything. Only `SchemaManager` (system code) may `DROP/CREATE SCHEMA`, and only for a schema
  carrying the agent's ownership marker (or an empty one); a non-empty foreign schema is refused.
* All generated SQL is checked against per-stage statement allow-lists and a forbidden list (`DROP DATABASE/SCHEMA/TABLE`,
  `CREATE SCHEMA/ROLE/EXTENSION`, `GRANT`, `TRUNCATE`, `COPY`, `search_path`/`ROLE` changes, `SECURITY DEFINER`, file/network
  functions, non-SQL/plpgsql languages, `public.` references). The connection pins `search_path`, and uses statement/lock/idle timeouts.
* **Limits:** the safety scanner is pattern based, not a SQL parser — it is a guardrail, not a sandbox. A generated function body
  can still run arbitrary DML inside the agent's schema. The database role is the real boundary; keep it unprivileged.
  No LLM-generated shell commands are ever executed (none are requested).
* Architecture normalization checks are heuristics (arrays, repeating groups, copied parent attributes); full 2NF/3NF dependency
  analysis is left to the LLM and reported in `normalization.analysis`.
* Only the Anthropic provider is implemented; there is no approval workflow, migration/diff mode, or multi-database support.
  Re-running resets the agent-owned schema (generation is from scratch each time).

## 15. Future Frontend Agent integration

```
Frontend Agent ──► requirements/entities.json ──► Database Agent ──► PostgreSQL
```

The contract is exactly the entity JSON in section 6 (`app/models/entity.py`). A Frontend Agent only has to emit a valid file;
the Database Agent validates it first and returns exit code `2` with the Pydantic error on violation, so no architectural change
is needed. The agent has no dependency on how the file was produced.
