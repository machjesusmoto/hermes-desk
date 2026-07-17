################################################################################
# components/hermes_display — LVGL UI manager (Tab5 BSP display + touch)
################################################################################
idf_component_register(
    SRCS
        "src/hermes_display.c"
    INCLUDE_DIRS
        "include"
    REQUIRES
        m5stack_tab5
        esp_lvgl_port
        esp_lcd_touch
        esp_lcd
        esp_driver_i2c
        lvgl
        log
        main
)
