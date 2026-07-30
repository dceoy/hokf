#!/usr/bin/env bash

set -euxo pipefail
cd "$(git rev-parse --show-toplevel)"

# Markdown
npx -y prettier --write './**/*.md'

# Shell scripts
git ls-files -z -- '*.sh' '*.bash' '*.bats' \
  | xargs -0 -t shfmt --write --indent=2 --binary-next-line --case-indent --space-redirects
git ls-files -z -- '*.sh' '*.bash' '*.bats' | xargs -0 -t shellcheck

# GitHub Actions
zizmor --fix=safe .github/workflows
git ls-files -z -- '.github/workflows/*.yml' '.github/workflows/*.yaml' | xargs -0 -t actionlint
git ls-files -z -- '.github/workflows/*.yml' '.github/workflows/*.yaml' | xargs -0 -t yamllint -d '{"extends": "relaxed", "rules": {"line-length": "disable"}}'
checkov --framework=all --output=github_failed_only --directory=.
