#!/usr/bin/env bash
# Ghép các parts thành file Python hoàn chỉnh
set -e
cd "$(dirname "$0")"
cat parts/part_01.py parts/part_02.py parts/part_03.py parts/part_04.py > cup_bot_hoanchinh.py
echo "Đã ghép -> cup_bot_hoanchinh.py ($(wc -c < cup_bot_hoanchinh.py) bytes)"
