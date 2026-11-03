## 2025-02-12 - os.walk optimization by modifying dirs in-place
**Learning:** When using `os.walk` to traverse a directory tree, it's significantly faster to modify the yielded `dirs` list in-place (e.g. `dirs[:] = [d for d in dirs if not d.startswith(".")]`) rather than filtering post-traversal. This prevents os.walk from descending into those directories, avoiding the I/O and processing overhead of traversing unwanted subdirectories.
**Action:** Always modify `dirs` in-place when filtering directories with `os.walk` to skip unwanted subdirectories.

## 2025-02-12 - Appending to journal
**Learning:** When adding entries to the `.jules/bolt.md` journal file, make sure to append to the file rather than overwriting it, so as not to erase previous memory and learnings.
**Action:** Always use `cat << 'EOF' >> .jules/bolt.md` instead of `cat << 'EOF' > .jules/bolt.md` to append new journal entries.
