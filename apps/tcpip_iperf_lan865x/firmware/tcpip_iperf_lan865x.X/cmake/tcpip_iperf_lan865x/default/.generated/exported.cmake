set(DEPENDENT_MP_BIN2HEXtcpip_iperf_lan865x_default_fZZHodlt "c:/Program Files/Microchip/xc32/v5.10/bin/xc32-bin2hex.exe")
set(DEPENDENT_DEPENDENT_TARGET_ELFtcpip_iperf_lan865x_default_fZZHodlt ${CMAKE_CURRENT_LIST_DIR}/../../../../out/tcpip_iperf_lan865x/default.elf)
set(DEPENDENT_TARGET_DIRtcpip_iperf_lan865x_default_fZZHodlt ${CMAKE_CURRENT_LIST_DIR}/../../../../out/tcpip_iperf_lan865x)
set(DEPENDENT_BYPRODUCTStcpip_iperf_lan865x_default_fZZHodlt ${DEPENDENT_TARGET_DIRtcpip_iperf_lan865x_default_fZZHodlt}/${sourceFileNametcpip_iperf_lan865x_default_fZZHodlt}.c)
add_custom_command(
    OUTPUT ${DEPENDENT_TARGET_DIRtcpip_iperf_lan865x_default_fZZHodlt}/${sourceFileNametcpip_iperf_lan865x_default_fZZHodlt}.c
    COMMAND ${DEPENDENT_MP_BIN2HEXtcpip_iperf_lan865x_default_fZZHodlt} --image ${DEPENDENT_DEPENDENT_TARGET_ELFtcpip_iperf_lan865x_default_fZZHodlt} --image-generated-c ${sourceFileNametcpip_iperf_lan865x_default_fZZHodlt}.c --image-generated-h ${sourceFileNametcpip_iperf_lan865x_default_fZZHodlt}.h --image-copy-mode ${modetcpip_iperf_lan865x_default_fZZHodlt} --image-offset ${addresstcpip_iperf_lan865x_default_fZZHodlt} 
    WORKING_DIRECTORY ${DEPENDENT_TARGET_DIRtcpip_iperf_lan865x_default_fZZHodlt}
    DEPENDS ${DEPENDENT_DEPENDENT_TARGET_ELFtcpip_iperf_lan865x_default_fZZHodlt})
add_custom_target(
    dependent_produced_source_artifacttcpip_iperf_lan865x_default_fZZHodlt 
    DEPENDS ${DEPENDENT_TARGET_DIRtcpip_iperf_lan865x_default_fZZHodlt}/${sourceFileNametcpip_iperf_lan865x_default_fZZHodlt}.c
    )
