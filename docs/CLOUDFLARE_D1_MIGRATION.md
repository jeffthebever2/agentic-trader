# Cloudflare D1 Migration

Agentic Trader can now use Cloudflare D1 for the user registry and per-user
portfolio blobs. Supabase remains supported as the fallback.

## Runtime Selection

Storage priority:

1. Cloudflare D1, when all are set:
   - `CLOUDFLARE_ACCOUNT_ID`
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_D1_DATABASE_ID`
2. Supabase, when D1 is not configured and these are set:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
3. Local JSON files in `tmp/`

## Create The D1 Database

```bash
wrangler d1 create agentic-trader
```

Copy the returned database ID into Settings or `.env`:

```bash
CLOUDFLARE_D1_DATABASE_ID=<database id>
```

The app creates the required tables automatically on first use.

## Required API Token Scopes

Use a Cloudflare API token scoped to your account with:

- Workers AI permission for model calls.
- D1 edit/query permission for database reads and writes.

Keep using a restricted account token, not your global API key.

## Move Existing Local Users Into D1

After setting the D1 env values:

```bash
python3 scripts/sync_users_to_d1.py
```

Manual portfolio blobs are created as users save portfolio state through the app.

## Keep Supabase For Now

Do not delete Supabase yet. Let D1 run for a few days, then export/backup before
removing Supabase credentials. If D1 fails, the app falls back to Supabase/local
storage so users are not locked out.
