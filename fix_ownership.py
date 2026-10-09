import tomlkit

with open("config/bsr-upstream-ownership.toml", "r") as f:
    doc = tomlkit.load(f)

# The duplicate entry was added by me previously as:
# [[ownership]]
# path = "src/semantic_release/bsr/registry.py"

# We should see if the original bsr-core entry is still there.
entries = doc.get("ownership", [])
for entry in entries:
    if entry.get("path") == "src/semantic_release/bsr/registry.py":
        print(entry)
