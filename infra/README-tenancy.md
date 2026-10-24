# Tenancy: database roles

The API must not connect as a superuser, a BYPASSRLS role, a table owner, or a
member of a privileged role. Its startup assertion refuses such login roles,
because any of these would bypass row-level security. Migrations run as the
owner role; the API runs as a separate login role.

The API login role must also not own the database or the schema (for example
`public`) that holds the tenancy tables, and must not be a member of their owner
roles: an owner can drop tables or disable RLS. Startup refuses a login role
(or session user) that owns or can assume ownership of any of these.

## Supported path: `python -m trendscend.db.bootstrap`

Run once per deploy, as a one-off job, with owner credentials:

```sh
OWNER_DATABASE_URL=postgresql://<owner-or-superuser>:<pw>@<host>/<db> \
API_DB_ROLE=trendscend_api \
API_DB_PASSWORD=<from secret manager> \
  python -m trendscend.db.bootstrap
```

- `OWNER_DATABASE_URL`: an owner/superuser DSN used **only** for this command.
  Never give it to the API container.
- `API_DB_ROLE`: login role name, `^[a-z_][a-z0-9_]{0,62}$`; `pg_*`,
  `trendscend_app`, `postgres` and the owner's own name are refused.
- `API_DB_PASSWORD`: the role's password (secret manager only, never committed).

The command applies pending migrations as the owner, then creates or repairs the
login role: `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB
NOREPLICATION INHERIT`, password reset, role-level settings cleared, member of
`trendscend_app` only (all other memberships revoked). It is idempotent, so
re-running it also repairs drift. It prints only `ok`; failures print a generic
message and never the password. Keep server `log_statement` off for `ddl`/`all`
during the run (the statement text contains the password), and check that
`password_encryption` is `scram-sha-256` so the password is stored hashed.

Who runs what: the **owner role** runs migrations and the bootstrap; the
**login role** (`API_DB_ROLE`, via the API's `DATABASE_URL`) serves requests.
`trendscend_app` is created by the migrations and holds the table privileges.

Reference only (what the command does, in SQL; prefer the command):

```sql
CREATE ROLE trendscend_api LOGIN PASSWORD '<from secret manager>'
  NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION INHERIT;
GRANT trendscend_app TO trendscend_api;
```

## Warning: edited migrations are not re-applied

The migration runner records applied versions without a checksum. Editing a
migration that has already been applied does nothing on that database. Any DB
that applied an earlier version of `002` must be rebuilt (or fixed with a new,
numbered migration).
