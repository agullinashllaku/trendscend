# Tenancy: database roles

The API must not connect as a superuser, a BYPASSRLS role, a table owner, or a
member of a privileged role. Its startup assertion refuses such login roles,
because any of these would bypass row-level security. Migrations run as the
owner role; the API runs as a separate login role.

Create the application login role (run as the owner/superuser):

```sql
CREATE ROLE trendscend_api LOGIN PASSWORD '<from secret manager>'
  NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
GRANT trendscend_app TO trendscend_api;
```

`trendscend_app` is created by the migrations and holds the table privileges.
Never commit the password; load it from the secret manager into the API's
`DATABASE_URL`.

## Warning: edited migrations are not re-applied

The migration runner records applied versions without a checksum. Editing a
migration that has already been applied does nothing on that database. Any DB
that applied an earlier version of `002` must be rebuilt (or fixed with a new,
numbered migration).
