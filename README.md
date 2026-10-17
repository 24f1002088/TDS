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

## Hints

- Use `grep -m 1` to get the first matching line
- Use `tr '/' '-'` to replace slashes with dashes
- Use `find`, `grep`, `cut`, `mkdir`, `mv` commands
- Quote paths properly to handle spaces and special characters
