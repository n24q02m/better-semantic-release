1. **Optimize `os.walk` in `src/semantic_release/changelog/template.py`**
   - The current implementation of `recursive_render` uses a generator expression with `os.walk`, checking `not any(elem.startswith(".") for elem in Path(root).relative_to(template_dir).parts)` for every file. This means `os.walk` descends into hidden directories (like `.git`, `.venv`) unnecessarily, which can be thousands of files, and checks each file post-traversal.
   - Modify `recursive_render` to iterate over `os.walk` using a `for` loop and mutate the `dirs` list in-place (`dirs[:] = [d for d in dirs if not d.startswith(".")]`). This instructs `os.walk` to skip hidden directories entirely, vastly improving performance on larger repositories.
2. **Update `config/bsr-upstream-ownership.toml`**
   - Register `src/semantic_release/changelog/template.py` in the ownership config with owner `bsr-stock-patch` and gates `["python-lint", "python-format", "python-type", "python-test"]` to track the divergence.
3. **Journal the Learning**
   - Append the learning about `os.walk` in-place directory mutation to `.jules/bolt.md`.
4. **Format and Test**
   - Run `ruff format src/semantic_release/changelog/template.py`.
   - Run the full test suite `uv run pytest tests/` to ensure the change introduces no regressions.
5. **Complete pre-commit steps**
   - Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.
6. **Submit Pull Request**
   - Create a PR with the title `⚡ Bolt: Optimize template discovery by skipping hidden directories during traversal` and a description detailing the measurable performance impact.
