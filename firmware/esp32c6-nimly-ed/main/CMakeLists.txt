# SPDX-License-Identifier: MIT
idf_component_register(
    SRCS "nimly_ed.c" "uart_bridge.c" "ota_uart.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES nvs_flash esp_app_format esp_driver_uart esp_driver_gpio app_update mbedtls esp_timer
)
