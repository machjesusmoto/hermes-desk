################################################################################
# components/hermes_ws — WebSocket client wrapper for the bridge
################################################################################
idf_component_register(
    SRCS
        "src/hermes_ws.c"
    INCLUDE_DIRS
        "include"
    REQUIRES
        esp_websocket_client
        esp_event
        esp_wifi
        esp_netif
        esp_log
        nvs_flash
)
