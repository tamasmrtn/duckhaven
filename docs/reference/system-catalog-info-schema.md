---
type: reference
---

# info_schema tables

Every column of the `duckhaven.info_schema` tables, which any member of a workspace queries in SQL to list its catalogs,
schemas, tables, columns, macros and semantic models. Each query sees only the rows that everyone it runs for can see;
[What info_schema shows](../querying/system-catalog.md#what-info_schema-shows) says which rows those are, and how soon a
change appears. How often each table is refreshed, and the rules every table follows, are in
[System catalog tables](system-catalog.md).

## info_schema.catalogs

One row per catalog attached to the workspace the query runs in, whatever the grants on it.

| Column | Type | Meaning |
|---|---|---|
| `workspace_id` | `UUID` | The workspace |
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | The catalog, by the name queries use |
| `catalog_kind` | `VARCHAR` | `iceberg_polaris` or `ducklake`; see [Catalog kinds](../data/catalogs.md#catalog-kinds) |
| `access_mode` | `VARCHAR` | `open` or `scoped`, the [access mode](../access/scoped-access.md) of this attachment |
| `is_default` | `BOOLEAN` | Whether it is the workspace's default catalog |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When the catalog was created |
| `attached_at` | `TIMESTAMP WITH TIME ZONE` | When it was attached to the workspace |

## info_schema.schemata

One row per schema you can see.

| Column | Type | Meaning |
|---|---|---|
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its catalog |
| `schema_name` | `VARCHAR` | The schema |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was last read from the catalog |

## info_schema.tables

One row per table you can see.

| Column | Type | Meaning |
|---|---|---|
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its catalog |
| `schema_name`, `table_name` | `VARCHAR` | Its schema and name |
| `table_type` | `VARCHAR` | `BASE TABLE` |
| `table_format` | `VARCHAR` | `ICEBERG` or `DUCKLAKE` |
| `column_count` | `INTEGER` | Its top-level columns |
| `row_count` | `BIGINT` | Its rows, as its page in the catalog tree shows them |
| `bytes` | `BIGINT` | The size of its data files, as its page shows it |
| `table_owner` | `VARCHAR` | The email of whoever created it on the **Catalog** page; empty for a table created any other way |
| `last_altered` | `TIMESTAMP WITH TIME ZONE` | Its last change: for an Iceberg table, by any engine; for a DuckLake table, the last DuckHaven saw after a write |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was last read from the catalog |

## info_schema.columns

One row per top-level column of each table you can see. A nested column's fields are in its `data_type`.

| Column | Type | Meaning |
|---|---|---|
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its table's catalog |
| `schema_name`, `table_name` | `VARCHAR` | Its table |
| `column_name` | `VARCHAR` | The column |
| `ordinal_position` | `INTEGER` | Its place in the table, from 1 |
| `data_type` | `VARCHAR` | Its type as `DESCRIBE` shows it to a query, such as `BIGINT` or `STRUCT(x INTEGER, y VARCHAR)` |
| `source_type` | `VARCHAR` | Its type as the catalog stores it, such as Iceberg's `long` or DuckLake's `int64` |
| `is_nullable` | `VARCHAR` | `YES` or `NO` |

## info_schema.routines

One row per macro, per parameter list, of each DuckLake schema whose macros you can see.

| Column | Type | Meaning |
|---|---|---|
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its catalog |
| `schema_name`, `routine_name` | `VARCHAR` | Its schema and name |
| `overload` | `INTEGER` | Which of its parameter lists, from 0; a macro defined with several has a row for each |
| `routine_type` | `VARCHAR` | `macro`, which returns a value, or `table_macro`, which returns a table |
| `parameters` | `VARCHAR` | Its parameters, comma-separated, each with its type when it was given one, such as `n INTEGER`. Defaults are not shown |
| `definition` | `VARCHAR` | Its body, as DuckLake stores it |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was last read from the catalog |

## info_schema.semantic_models

One row per [semantic model](../metadata/semantic-layer.md) of the query's workspace that you can see, at any status.
The other `semantic_` tables hold the parts of the models this one shows.

| Column | Type | Meaning |
|---|---|---|
| `model_id`, `workspace_id` | `UUID` | The model, and its workspace |
| `model_slug`, `name` | `VARCHAR` | Its slug, as the API and the CLI name it, and its display name |
| `description` | `VARCHAR` | What it covers |
| `status` | `VARCHAR` | `draft`, `published` or `deprecated` |
| `provider` | `VARCHAR` | `native` for a model made in DuckHaven, or the importer that made it, such as `dbt` |
| `owner_id`, `owner_email` | `UUID`, `VARCHAR` | Its owner |
| `created_at`, `updated_at` | `TIMESTAMP WITH TIME ZONE` | When it was created and last changed |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was copied |

## info_schema.semantic_datasets

One row per dataset: the table a model builds on.

| Column | Type | Meaning |
|---|---|---|
| `dataset_id` | `UUID` | The dataset |
| `model_id`, `workspace_id`, `model_slug` | `UUID`, `UUID`, `VARCHAR` | Its model, as in `semantic_models` |
| `name`, `description` | `VARCHAR` | Its name in the model, and what it holds |
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its table's catalog |
| `schema_name`, `table_name` | `VARCHAR` | Its table |
| `primary_key` | `VARCHAR`, JSON | The columns that identify a row, as a list |
| `synonyms` | `VARCHAR`, JSON | Other names for it, as a list |
| `validation_state` | `VARCHAR` | `ok`, `broken` when its table does not match it, or `unchecked` |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was copied |

## info_schema.semantic_dimensions

One row per dimension: a column, or an expression, to group or filter by.

| Column | Type | Meaning |
|---|---|---|
| `dimension_id` | `UUID` | The dimension |
| `model_id`, `workspace_id`, `model_slug` | `UUID`, `UUID`, `VARCHAR` | Its model, as in `semantic_models` |
| `dataset_id`, `dataset_name` | `UUID`, `VARCHAR` | Its dataset |
| `name`, `display_name`, `description` | `VARCHAR` | Its name, its label and what it means |
| `kind` | `VARCHAR` | `categorical` or `time` |
| `expr` | `VARCHAR` | Its SQL expression |
| `data_type` | `VARCHAR` | Its type, when known |
| `time_grains` | `VARCHAR`, JSON | For a time dimension, the grains it may be grouped by: `day`, `week`, `month`, `quarter`, `year` |
| `is_default_time` | `BOOLEAN` | Whether it is its dataset's default time dimension |
| `synonyms` | `VARCHAR`, JSON | Other names for it, as a list |
| `validation_state` | `VARCHAR` | `ok`, `broken` or `unchecked` |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was copied |

## info_schema.semantic_metrics

One row per metric. See [Define metrics](../metadata/define-metrics.md) for what each field does.

| Column | Type | Meaning |
|---|---|---|
| `metric_id` | `UUID` | The metric |
| `model_id`, `workspace_id`, `model_slug` | `UUID`, `UUID`, `VARCHAR` | Its model, as in `semantic_models` |
| `dataset_id`, `dataset_name` | `UUID`, `VARCHAR` | Its dataset |
| `name`, `display_name`, `description` | `VARCHAR` | Its name, its label and what it means |
| `agg` | `VARCHAR` | `sum`, `count`, `count_distinct`, `avg`, `min` or `max` |
| `expr`, `filter` | `VARCHAR` | The SQL expression it aggregates, and the condition rows must meet |
| `time_dimension_id` | `UUID` | The time dimension it is measured over, as `dimension_id` in `semantic_dimensions` |
| `format` | `VARCHAR` | `number`, `percent`, `currency`, `duration` or `bytes` |
| `unit`, `currency` | `VARCHAR` | Its unit, and for money its currency code |
| `polarity` | `VARCHAR` | `up_good`, `down_good` or `neutral` |
| `caveat` | `VARCHAR` | What to keep in mind reading it |
| `synonyms` | `VARCHAR`, JSON | Other names for it, as a list |
| `status` | `VARCHAR` | `draft`, `published` or `deprecated` |
| `owner_id`, `owner_email` | `UUID`, `VARCHAR` | Its owner |
| `validation_state` | `VARCHAR` | `ok`, `broken` or `unchecked` |
| `created_at`, `updated_at` | `TIMESTAMP WITH TIME ZONE` | When it was created and last changed |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was copied |

## info_schema.semantic_relationships

One row per declared join between two datasets of a model.

| Column | Type | Meaning |
|---|---|---|
| `relationship_id` | `UUID` | The relationship |
| `model_id`, `workspace_id`, `model_slug` | `UUID`, `UUID`, `VARCHAR` | Its model, as in `semantic_models` |
| `name` | `VARCHAR` | Its name |
| `left_dataset_id`, `left_dataset_name` | `UUID`, `VARCHAR` | The dataset it joins from |
| `right_dataset_id`, `right_dataset_name` | `UUID`, `VARCHAR` | The dataset it joins to |
| `join_columns` | `VARCHAR`, JSON | The columns joined, as a list of `{"left": …, "right": …}` |
| `cardinality` | `VARCHAR` | `many_to_one` or `one_to_one` |
| `validation_state` | `VARCHAR` | `ok`, `broken` or `unchecked` |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was copied |

## Related

- [System catalog tables](system-catalog.md): every table, the permission it needs, and how fresh it is.
- [System catalog](../querying/system-catalog.md): what `info_schema` shows each person, and how a query reads it.
- [Semantic layer](../metadata/semantic-layer.md): what models, datasets, dimensions, metrics and relationships are.
