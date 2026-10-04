POSTGRESQL RULES (apply to every artifact you produce)
- Target server: the PostgreSQL version given in the input. Use only features available in it.
- Naming: snake_case, plural table names (users, order_items), singular column names, foreign key columns
  named <singular_parent>_id. Never use quoted identifiers. Never use PostgreSQL reserved words as names
  (e.g. user, order, group, table, desc, check, default, primary, references). Max 63 characters.
- All objects live in ONE schema that is already selected through search_path. Always use UNQUALIFIED
  names. Never write a schema prefix (no public., no myschema.), never create/drop/alter schemas, never
  change search_path.
- Entity field type mapping (entity type -> PostgreSQL type):
  integer -> integer; string -> text (add CHECK constraints for non-empty / maximum length where sensible),
  or varchar(n) when a maximum length is clearly implied; text -> text; decimal -> numeric(p,s)
  (money: numeric(12,2)); boolean -> boolean; datetime -> timestamptz; date -> date; uuid -> uuid.
- Surrogate primary keys named id use `integer GENERATED ALWAYS AS IDENTITY` (a column referencing them
  uses plain `integer`; types of foreign key and referenced column MUST be identical in base type).
- Prefer declarative integrity (PRIMARY KEY, FOREIGN KEY, UNIQUE, NOT NULL, CHECK, DEFAULT) over procedural code.
- Timestamps: created_at timestamptz NOT NULL DEFAULT now() is appropriate for records whose creation
  time is not supplied by the entity; do not add timestamps that duplicate an existing entity field.
