# SPDX-License-Identifier: MIT
cmake_minimum_required(VERSION 3.16)

# Fast programvaruversion (rapporteras till HA på nimly/info).
if(NOT DEFINED PROJECT_VER)
    set(PROJECT_VER "0.5.1")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(esp32-uart-mqtt-bridge)
