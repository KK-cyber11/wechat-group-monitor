#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
swiftc -O -o passive_ocr passive_ocr.swift
swiftc -O -o wx_input wx_input.swift
echo "compiled"
