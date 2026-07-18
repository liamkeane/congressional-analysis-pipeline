# Roadmap

## Phase 0: Setup & scoping (few hours)

1. Get your Congress.gov API key (already done)
2. Decide scope up front: 119th Congress only, HR/S bill types only for v1 — write this down in your README as an explicit decision, not an oversight
3. Set up the repo skeleton, .gitignore, .env pattern (already done)
4. Resource: Congress.gov API docs and their GitHub repo with example requests — worth skimming the whole endpoint list once so you know what's available before you commit to a schema

## Phase 1: Raw extraction (few days)

1. Build out the rest of your extract functions (you have bills/actions/cosponsors/subjects; add members, committees)
2. Write to local disk first, structured exactly like you'd write to Blob (raw/{entity}/dt=YYYY-MM-DD/) — this makes swapping storage backends later trivial
3. Handle pagination and backoff properly (you've got the pattern already)
4. Resource: nothing fancy needed here — requests + pathlib is genuinely enough. If you want a slightly more robust HTTP layer later, tenacity (retry decorator library) is a common upgrade from hand-rolled backoff and is a nice name to drop in an interview

## Phase 2: Move raw data to real cloud storage

1. Swap local raw_data/ writes for Azure Blob SDK (azure-storage-blob package)
2. Same partitioning scheme, just a different write target
3. Resource: Azure Blob Storage Python SDK quickstart — the SDK is straightforward if you've used boto3 or similar before

## Phase 3: Load raw → Snowflake

1. Set up a Snowflake trial account, a database/schema for raw landing tables, and a Snowflake external stage pointing at your Blob container
2. Decide: COPY INTO triggered manually/by Airflow, or Snowpipe (auto-ingest on new file arrival). I'd start with plain COPY INTO orchestrated by Airflow — simpler to reason about and debug, and Snowpipe is an easy "future improvement" bullet point later
3. Raw tables should be near-schemaless — mostly VARIANT/JSON columns, minimal transformation at this stage. All real transformation happens in dbt next
4. Resource: Snowflake's docs on loading data from Azure — specifically the external stage + COPY INTO pages

## Phase 4: dbt transformation

1. Init a dbt project, connect it to Snowflake (dbt-snowflake adapter)
2. Build layer by layer: staging (1:1 with raw, light renaming/casting) → intermediate (joins, the status-derivation logic we discussed) → marts (your fact/dim tables)
3. Add a dbt snapshot for dim_member and dim_bill (your SCD Type 2 tables)
4. Add tests as you go, not at the end — not_null/unique/relationships on every model minimum, plus a few custom ones (e.g., cosponsor count never negative)
5. Resource: dbt's own Jaffle Shop tutorial is the standard onramp even for experienced folks, just to see their idiomatic project structure once — you'll finish it in an afternoon. Their docs on incremental models and snapshots are the two pages you'll reference most

## Phase 5: Orchestration with Airflow

1. Local Airflow via Docker Compose (their official quickstart) — don't bother with a managed Airflow service for a portfolio project, it's unnecessary cost/complexity
2. Build the DAG structure we sketched: extract tasks → load → dbt run → dbt test, with a failure callback
3. Use the TaskFlow API (@task decorators) rather than old-style PythonOperator boilerplate — cleaner code, and it's the modern idiom interviewers expect to see now
4. Resource: Airflow's official Docker Compose quickstart, and the Astronomer Cosmos library if you want Airflow to natively understand and render your dbt DAG as individual tasks rather than one opaque "run dbt" blob — this is a nice, fairly easy addition that shows polish

## Phase 6: Testing & data quality story

1. Formalize your dbt tests, maybe add a small "data quality mart" flagging logical inconsistencies (bill marked BecameLaw with no prior President action, etc.)
2. Resource: you likely don't need Great Expectations on top of dbt tests for a project this size — that'd be redundant tooling. dbt's built-in + dbt-utils package tests are enough; mentioning you deliberately scoped out GE to avoid tool sprawl is itself a good interview answer

## Phase 7: Dashboard

1. Streamlit app on top of your marts (direct Snowflake connector, snowflake-connector-python or snowflake-sqlalchemy)
2. Build the 3-4 charts around whichever analytical angle you pick from last message
3. Deploy to Streamlit Community Cloud for a live link
4. Resource: Streamlit's docs on connecting to Snowflake — there's a first-party guide

### Aggregation and trend analysis
- Legislative velocity — how long do bills sit in each stage (introduced → committee → floor → law) by policy area or by Congress? Is Congress getting slower or faster over time?
- Bipartisanship trends — cross-party cosponsorship rate by policy area over time. Which topics still get bipartisan support, which have become purely partisan?
- Committee bottlenecks — which committees have the lowest "bills reported out" rate? Where do bills go to die?
- Cosponsorship network — which members frequently cosponsor together across party lines (a graph/network view, which is visually distinctive and not something a bill-lookup site would show)
- Policy attention over time — which subject areas are rising or falling in bill volume year over year (I'm especially interested in AI-related bills/lack thereof)

## Phase 8: Documentation & polish

1. README with architecture diagram (a simple draw.io or Excalidraw diagram is fine, don't overbuild this), key design decisions and trade-offs, known limitations, and how to run it
2. Resource: Excalidraw is the fastest way to make a clean architecture diagram without fighting a heavyweight tool