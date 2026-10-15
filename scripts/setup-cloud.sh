#!/usr/bin/env bash
set -euo pipefail
cd /workspace/hl-dacdesky
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
command -v tesseract >/dev/null
mkdir -p /workspace/.monitor-tessdata
curl --fail --silent --show-error -L --max-time 60 \
  https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/ces.traineddata \
  -o /workspace/.monitor-tessdata/ces.traineddata
printf '%s\n' '934bcaf97ef3348413263331131c9fa7f55f30db333c711929c124fb635f7e1b  /workspace/.monitor-tessdata/ces.traineddata' | sha256sum -c -
TESSDATA_PREFIX=/workspace/.monitor-tessdata .venv/bin/python -m unittest discover -s tests -v
