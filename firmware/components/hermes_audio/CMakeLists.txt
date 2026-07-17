################################################################################
# components/hermes_audio — I2S capture (ES7210) + playback (ES8388) via BSP
################################################################################
idf_component_register(
    SRCS
        "src/hermes_audio.c"
    INCLUDE_DIRS
        "include"
    REQUIRES
        m5stack_tab5
        esp_codec_dev
        log
    PRIV_REQUIRES
        main
)
