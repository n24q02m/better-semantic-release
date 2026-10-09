with open("config/bsr-upstream-ownership.toml", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "[[ownership]]" in line and skip:
        skip = False # we don't do this here

    # We want to remove the exact lines we added at the end:
    # [[ownership]]
    # path = "src/semantic_release/bsr/registry.py"
    # status = "M"
    # kind = "python-source"
    # owner = "bsr-stock-patch"
    # reason = "Security improvement: robust SSRF protection via getaddrinfo"
    # gates = ["python-lint", "python-format", "python-test", "python-type"]

# Let's just truncate the file by 9 lines (since we appended exactly 9 lines).
# Let's check the last 10 lines first.
