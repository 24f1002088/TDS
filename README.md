# Knowledge Base Reorganization

## Task

You have 30 text files scattered across a nested directory structure. Your job is to reorganize them into a flat structure based on categories.

**Rules:**
1. Read the **first line** in each .txt file that matches `category: ...`
2. Create a directory for each category
3. Move each file to: `{category}/{path-with-dashes}-{filename}`
   - Replace all `/` in the original path with `-`
   - Keep the filename as-is

**Example:**
- Original: `docs/chapter1/file01.txt` with first line `category: reports`
- New location: `reports/docs-chapter1-file01.txt`

**Example:**
- Original: `project/part 2/intro/file15.txt` with first line `category: documentation`
- New location: `documentation/project-part 2-intro-file15.txt`

## Instructions

1. Extract the ZIP file
2. Use shell commands (bash) to reorganize the files
3. After reorganization, run: `find . -type f | LC_ALL=C sort | sha256sum`
4. Submit the hash (first part before the space/dash)

## Important Notes

- Some filenames/paths contain **Unicode characters**, **spaces**, and **special characters**
- Some categories use **non-ASCII characters** (accents, diacritics, etc.)
- Use `LC_ALL=C` to ensure consistent sorting regardless of locale
- The hash must match exactly, including directory structure

## Invoice A2A agent

The FastAPI service also exposes the invoice action agent at `/a2a/`:

- `GET /.well-known/agent-card.json`
- `POST /a2a/message:send`
- `GET /a2a/tasks` and `GET /a2a/tasks/{id}`
- `POST /a2a/tasks/{id}:cancel`

Set `A2A_BEARER_TOKEN` to a deployment-specific secret before making the service public.
The task store is SQLite-backed; set `A2A_DB_PATH` to persistent storage when the host
does not preserve the application directory. The Render configuration starts the service
on the platform-provided HTTPS origin.

The mailroom action gate is available at `/mailroom` (and the root endpoint dispatches
mailroom envelopes by their profile). Set `MAILROOM_DB_PATH` to persistent storage so
proposal and signed-receipt state survives restarts. It requires the `cryptography`
package to verify each Ed25519 receipt before returning an executed outcome.

## Hints

- Use `grep -m 1` to get the first matching line
- Use `tr '/' '-'` to replace slashes with dashes
- Use `find`, `grep`, `cut`, `mkdir`, `mv` commands
- Quote paths properly to handle spaces and special characters
