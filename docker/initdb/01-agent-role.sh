#!/bin/bash
# Creates the restricted role + dedicated database the agent works in.
# The role is NOT a superuser: it cannot create roles/databases and owns only database_agent.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -v pw="$AGENT_DB_PASSWORD" --username "$POSTGRES_USER" --dbname postgres <<'SQL'
CREATE ROLE agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'pw';
CREATE DATABASE database_agent OWNER agent;
\c database_agent
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
