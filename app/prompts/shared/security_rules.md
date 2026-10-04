SECURITY RULES
- Output is machine-executed against a dedicated development database. It is statically checked and REJECTED if it
  contains: DROP DATABASE / SCHEMA / TABLE / INDEX / TYPE / TRIGGER, CREATE SCHEMA / DATABASE / ROLE / EXTENSION,
  ALTER SYSTEM / ROLE / DATABASE, GRANT / REVOKE, TRUNCATE, COPY, SET search_path / ROLE, RESET, SECURITY DEFINER,
  server file or network functions (pg_read_file, lo_import, dblink, ...), languages other than SQL and plpgsql,
  or any reference to the public schema.
- Never build SQL by string concatenation of caller input inside functions (no EXECUTE with concatenated input).
  Use parameters.
- Never output shell commands, credentials, or secrets.
