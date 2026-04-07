include("${CMAKE_CURRENT_LIST_DIR}/rule.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/file.cmake")

set(tcpip_iperf_lan865x_default_library_list )

# Handle files with suffix s, for group default-XC32
if(tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_assemble)
add_library(tcpip_iperf_lan865x_default_default_XC32_assemble OBJECT ${tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_assemble})
    tcpip_iperf_lan865x_default_default_XC32_assemble_rule(tcpip_iperf_lan865x_default_default_XC32_assemble)
    list(APPEND tcpip_iperf_lan865x_default_library_list "$<TARGET_OBJECTS:tcpip_iperf_lan865x_default_default_XC32_assemble>")

endif()

# Handle files with suffix S, for group default-XC32
if(tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_assembleWithPreprocess)
add_library(tcpip_iperf_lan865x_default_default_XC32_assembleWithPreprocess OBJECT ${tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_assembleWithPreprocess})
    tcpip_iperf_lan865x_default_default_XC32_assembleWithPreprocess_rule(tcpip_iperf_lan865x_default_default_XC32_assembleWithPreprocess)
    list(APPEND tcpip_iperf_lan865x_default_library_list "$<TARGET_OBJECTS:tcpip_iperf_lan865x_default_default_XC32_assembleWithPreprocess>")

endif()

# Handle files with suffix [cC], for group default-XC32
if(tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_compile)
add_library(tcpip_iperf_lan865x_default_default_XC32_compile OBJECT ${tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_compile})
    tcpip_iperf_lan865x_default_default_XC32_compile_rule(tcpip_iperf_lan865x_default_default_XC32_compile)
    list(APPEND tcpip_iperf_lan865x_default_library_list "$<TARGET_OBJECTS:tcpip_iperf_lan865x_default_default_XC32_compile>")

endif()

# Handle files with suffix cpp, for group default-XC32
if(tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_compile_cpp)
add_library(tcpip_iperf_lan865x_default_default_XC32_compile_cpp OBJECT ${tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_compile_cpp})
    tcpip_iperf_lan865x_default_default_XC32_compile_cpp_rule(tcpip_iperf_lan865x_default_default_XC32_compile_cpp)
    list(APPEND tcpip_iperf_lan865x_default_library_list "$<TARGET_OBJECTS:tcpip_iperf_lan865x_default_default_XC32_compile_cpp>")

endif()

# Handle files with suffix [cC], for group default-XC32
if(tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_dependentObject)
add_library(tcpip_iperf_lan865x_default_default_XC32_dependentObject OBJECT ${tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_dependentObject})
    tcpip_iperf_lan865x_default_default_XC32_dependentObject_rule(tcpip_iperf_lan865x_default_default_XC32_dependentObject)
    list(APPEND tcpip_iperf_lan865x_default_library_list "$<TARGET_OBJECTS:tcpip_iperf_lan865x_default_default_XC32_dependentObject>")

endif()


# Main target for this project
add_executable(tcpip_iperf_lan865x_default_image_fZZHodlt ${tcpip_iperf_lan865x_default_library_list})

set_target_properties(tcpip_iperf_lan865x_default_image_fZZHodlt PROPERTIES
    OUTPUT_NAME "default"
    SUFFIX ".elf"
    RUNTIME_OUTPUT_DIRECTORY "${tcpip_iperf_lan865x_default_output_dir}")
target_link_libraries(tcpip_iperf_lan865x_default_image_fZZHodlt PRIVATE ${tcpip_iperf_lan865x_default_default_XC32_FILE_TYPE_link})

# Add the link options from the rule file.
tcpip_iperf_lan865x_default_link_rule( tcpip_iperf_lan865x_default_image_fZZHodlt)

# Add bin2hex target for converting built file to a .hex file.
string(REGEX REPLACE [.]elf$ .hex tcpip_iperf_lan865x_default_image_name_hex ${tcpip_iperf_lan865x_default_image_name})
add_custom_target(tcpip_iperf_lan865x_default_Bin2Hex ALL
    COMMAND ${MP_BIN2HEX} \"${tcpip_iperf_lan865x_default_output_dir}/${tcpip_iperf_lan865x_default_image_name}\"
    BYPRODUCTS ${tcpip_iperf_lan865x_default_output_dir}/${tcpip_iperf_lan865x_default_image_name_hex}
    COMMENT "Convert built file to .hex")
add_dependencies(tcpip_iperf_lan865x_default_Bin2Hex tcpip_iperf_lan865x_default_image_fZZHodlt)



