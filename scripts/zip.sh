#!/usr/bin/env bash
set -euxo pipefail
OUTPUT=".vscode/order-teacher.zip"
[ -f $OUTPUT ] && rm $OUTPUT
zip -x "*/__pycache__/*" "__pycache__/*" "tests/data/*" -r $OUTPUT scripts/ src/ tests/ .env .gitignore openapi.yaml pyproject.toml redocly.yaml
