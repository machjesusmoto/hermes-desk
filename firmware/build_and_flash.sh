#!/bin/bash
# Build and flash hermes-desk firmware
# Usage: ./build_and_flash.sh
set -e
export PATH="/home/dtaylor/.espressif/tools/riscv32-esp-elf/esp-14.2.0_20260121/riscv32-esp-elf/bin:$PATH"
export IDF_PATH=/home/dtaylor/esp-idf
export ESP_IDF_VERSION=5.5
source /home/dtaylor/.espressif/python_env/idf5.5_py3.14_env/bin/activate
export PATH="$IDF_PATH/tools:$PATH"
cd /home/dtaylor/hermes-desk/firmware
echo "=== Building ==="
idf.py build 2>&1 | tail -5
echo "=== Flashing ==="
idf.py -p /dev/ttyACM1 flash 2>&1 | tail -5
echo "=== Done ==="
