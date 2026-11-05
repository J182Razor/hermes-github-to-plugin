---
name: convert-repo
description: Use github-to-hermes tools to convert a GitHub repository into a Hermes plugin, then review and validate the generated output before submission.
---

# Convert a GitHub repo to a Hermes plugin

1. Run `detect_repo(repo_url=...)` to classify the repository.
2. Run `convert_repo(repo_url=..., name=...)` to generate a plugin scaffold.
3. Review generated files, especially manifests and disclosure.
4. Run `validate_plugin(path=...)` to ensure admission gates pass.
5. Polish README disclosure and tests before pushing.
PY