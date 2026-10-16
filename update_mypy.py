import tomlkit

with open("pyproject.toml", "r", encoding="utf-8") as f:
    doc = tomlkit.load(f)

files = doc["tool"]["mypy"]["files"]
if "src/semantic_release/changelog/template.py" not in files:
    files.append("src/semantic_release/changelog/template.py")

with open("pyproject.toml", "w", encoding="utf-8") as f:
    tomlkit.dump(doc, f)
