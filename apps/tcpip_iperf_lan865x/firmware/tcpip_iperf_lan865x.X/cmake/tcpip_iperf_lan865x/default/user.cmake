# user.cmake — do not remove, included by CMakeLists.txt after .generated/main.cmake
#
# Root cause: xc32-gcc is a MINGW-based binary.  When Ninja passes the linker
# -o argument on Windows it uses backslashes (e.g. "out\default.elf").
#
# Observed behavior — depends on xc32 / MINGW version:
#   OLD (xc32 <= v4.x):  MINGW strips backslash  → linker writes "outdefault.elf"
#   NEW (xc32 >= v5.10): MINGW fixed             → linker writes "out/default.elf"
#
# Both cases are handled by normalize_elf.cmake (generated below at configure time).
#
# Why RUNTIME_OUTPUT_DIRECTORY is kept:
#   Has no observable effect on the link command (Ninja still generates the
#   relative path), but kept as a forward-compatibility hint for future CMake
#   versions that might honour it.

file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/out")

# Add custom source files that are not tracked by MCC-generated file.cmake
target_sources(tcpip_iperf_lan865x_default_default_XC32_compile PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/ptp_log.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/loop_stats.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/ptp_offset_trace.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/sw_ntp.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/sw_ntp_offset_trace.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/tfuture.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/lan_regs_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/ptp_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/sw_ntp_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/tfuture_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/loop_stats_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/ptp_rx.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/cyclic_fire.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/cyclic_fire_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/pd10_blink.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/pd10_blink_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/button_led.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/standalone_demo.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/demo_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/iperf_control.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/cyclic_fire_isr.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/exception_handler.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/test_exception_cli.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/watchdog.c"
)

# Generate a small cmake helper that handles both MINGW behaviors at build time.
file(WRITE "${CMAKE_BINARY_DIR}/normalize_elf.cmake" [=[
# Invoked from POST_BUILD.  Variables passed via -D:
#   MANGLED_ELF  — ${CMAKE_BINARY_DIR}/outdefault.elf  (old MINGW: backslash stripped)
#   NORMAL_ELF   — ${CMAKE_BINARY_DIR}/out/default.elf (new MINGW: backslash intact)
if(NOT EXISTS "${NORMAL_ELF}")
    if(EXISTS "${MANGLED_ELF}")
        file(RENAME "${MANGLED_ELF}" "${NORMAL_ELF}")
        message(STATUS "ELF normalized: outdefault.elf -> out/default.elf  (old MINGW backslash fix)")
    else()
        message(FATAL_ERROR
            "Linker ELF not found at either expected location:\n"
            "  ${NORMAL_ELF}\n"
            "  ${MANGLED_ELF}")
    endif()
endif()
]=])

# Resolve the generated image target name dynamically (MCC regenerates a new
# random suffix each time, e.g. _fZZHodlt, _x_a8ivgm …).
file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/.generated/main.cmake" _main_cmake_lines
     REGEX "^add_executable\\(tcpip_iperf_lan865x_default_image_")
if(NOT _main_cmake_lines)
    message(FATAL_ERROR "user.cmake: could not locate image target in .generated/main.cmake")
endif()
string(REGEX MATCH "tcpip_iperf_lan865x_default_image_[A-Za-z0-9_]+" _image_target "${_main_cmake_lines}")

set_target_properties(${_image_target} PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/out")

add_custom_command(TARGET ${_image_target} POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E make_directory
        "${tcpip_iperf_lan865x_default_output_dir}"
    # Step 1: normalize ELF location (handles both old and new MINGW behavior)
    COMMAND "${CMAKE_COMMAND}"
        "-DMANGLED_ELF=${CMAKE_BINARY_DIR}/outdefault.elf"
        "-DNORMAL_ELF=${CMAKE_BINARY_DIR}/out/default.elf"
        -P "${CMAKE_BINARY_DIR}/normalize_elf.cmake"
    # Step 2: canonical location for xc32-bin2hex, flash.py, build_summary.py
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${CMAKE_BINARY_DIR}/out/default.elf"
        "${tcpip_iperf_lan865x_default_output_dir}/default.elf"
    COMMENT "Normalizing ELF location (xc32 MINGW backslash workaround)"
    VERBATIM)
