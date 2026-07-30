---
name: local-qa
description: Run local QA including formatting, linting, type-checking, static site build, and workflow checks for the repository. Use whenever any file has been updated.
disable-model-invocation: false
---

# Local QA (format, lint, type-check, and build)

Run the local QA script for this repository:

```bash
.agents/skills/local-qa/scripts/qa.sh
```

## Procedure

1. Execute the script exactly as shown above when this skill is triggered.
2. Capture and summarize key output: success/failure, major warnings, and any files modified.
3. If the script fails due to missing tooling (`command not found`, missing executable, or equivalent), install the missing tool(s) and rerun `.agents/skills/local-qa/scripts/qa.sh`.
4. Install tools using this order of preference:
   1. Use the project's package manager when applicable (`uv` for Python, package manager scripts/dependencies for Node.js).
   2. Use a system package manager (`brew` on macOS, `apt` on Debian/Ubuntu) when project-local install is not applicable.
   3. Use language-specific installers as fallback (`pipx`/`pip`, `npm`, `go install`, etc.).
5. If multiple tools are missing, repeat install -> rerun until QA completes or you hit a blocker.
6. If installation fails or requires unavailable privileges, report what was attempted, the exact failure, and stop.
7. Do not run unrelated commands; only run commands needed for QA and missing-tool installation.
