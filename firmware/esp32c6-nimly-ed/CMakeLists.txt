# SPDX-License-Identifier: MIT
# ESP32-C6 "Nimly ED" – Zigbee EndDevice som emulerar Nimly Connect Module (ZMNC010).
#
# Bygg:  idf.py set-target esp32c6 && idf.py build
# Flasha: idf.py -p <PORT> flash monitor
#
# Kräver ESP-IDF v5.5.x (rekommenderat) och esp-zigbee-lib v2.x (hämtas automatiskt
# via main/idf_component.yml).

cmake_minimum_required(VERSION 3.16)

# Fast programvaruversion (rapporteras i "hello" till integrationen).
if(NOT DEFINED PROJECT_VER)
    set(PROJECT_VER "0.5.4")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(nimly_ed)

# GCC 14.2 hits an internal compiler error in nvs_flash's C++ sources at the
# default -Og. Build just that component at -O1; changing the whole project's
# optimization breaks the RISC-V linker relaxation instead.
idf_component_set_property(nvs_flash COMPILE_OPTIONS -O1)
