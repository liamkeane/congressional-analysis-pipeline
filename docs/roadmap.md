# Roadmap

## Phase 0: Setup & scoping

1. Get your Congress.gov API key
2. Decide scope up front.
3. Set up the repo skeleton, .gitignore, .env pattern
4. Resource: Congress.gov API docs and their GitHub repo with example requests.

## Phase 1: Raw extraction

1. Build out extract functions.
2. Write to local disk first, but structured like writing to Blob (raw/{entity}/dt=YYYY-MM-DD/)
3. Handle pagination and backoff properly
4. Resources: requests + pathlib

## Phase 2: Move raw data to real cloud storage

1. Swap local raw_data/ writes for Azure Blob SDK
2. Same partitioning scheme, just a different write target
3. Resource: Azure Blob Storage Python SDK quickstart

## Phase 3: Load raw → Snowflake

1. Set up a Snowflake trial account, a database/schema for raw landing tables, and a Snowflake external stage pointing at your Blob container
2. Raw tables should be near-schemaless — mostly VARIANT/JSON columns, minimal transformation at this stage. All real transformation happens in dbt next
3. Resource: Snowflake's docs on loading data from Azure

## Phase 4: dbt transformation

1. Init a dbt project, connect it to Snowflake (dbt-snowflake adapter)
2. Build layer by layer: staging (1:1 with raw, light renaming/casting) → intermediate (joins, status-derivation logic) → marts (fact/dim tables)
3. Add a dbt snapshot for dim_member and dim_bill (SCD Type 2 tables)
4. Add tests throughout.
5. Resource: dbt's own Jaffle Shop tutorial and their docs on incremental models and snapshots

## Phase 5: Orchestration with Airflow

1. Local Airflow via Docker Compose
2. Build the DAG structure: extract tasks → load → dbt run → dbt test, with a failure callback
3. Use the TaskFlow API (@task decorators)
4. Resource: Airflow's official Docker Compose quickstart, and the Astronomer Cosmos library if you want Airflow to natively understand and render your dbt DAG as individual tasks rather than one opaque "run dbt" blob

## Phase 6: Testing & data quality story

1. Formalize your dbt tests
2. Resource: dbt's built-in + dbt-utils package tests

## Phase 7: Dashboard

1. Streamlit app on top of your marts (direct Snowflake connector, snowflake-connector-python or snowflake-sqlalchemy)
2. Build the 3-4 charts. Possible analysis items below.
3. Deploy to Streamlit Community Cloud for a live link
4. Resource: Streamlit's docs on connecting to Snowflake — there's a first-party guide

### Aggregation and trend analysis
- Legislative velocity — how long do bills sit in each stage (introduced → committee → floor → law) by policy area or by Congress? Is Congress getting slower or faster over time?
- Bipartisanship trends — cross-party cosponsorship rate by policy area over time. Which topics still get bipartisan support, which have become purely partisan?
- Committee bottlenecks — which committees have the lowest "bills reported out" rate? Where do bills go to die?
- Cosponsorship network — which members frequently cosponsor together across party lines (a graph/network view, which is visually distinctive and not something a bill-lookup site would show)
- Policy attention over time — which subject areas are rising or falling in bill volume year over year (I'm especially interested in AI-related bills/lack thereof)

## Phase 8: Documentation & polish

1. README with architecture diagram, key design decisions and trade-offs, known limitations, and how to run it
2. Resource: dbdiagram.io