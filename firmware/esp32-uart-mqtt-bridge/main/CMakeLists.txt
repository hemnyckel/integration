# SPDX-License-Identifier: MIT
idf_component_register(
    SRCS "main.c" "mqtt_bridge.c" "uart_link.c" "ota.c" "ota_c6.c" "improv_link.cpp"
    INCLUDE_DIRS "."
    PRIV_REQUIRES nvs_flash mqtt esp_wifi esp_netif esp_event esp_driver_uart esp_coex esp_app_format esp_http_client app_update bt mbedtls
)
