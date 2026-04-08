# user.cmake — do not remove, included by CMakeLists.txt after .generated/main.cmake
#
# Root cause: xc32-gcc is a MINGW-based binary.  When Ninja passes the linker
# -o argument on Windows it uses backslashes (e.g. "out\default.elf").  The
# MINGW ld strips every backslash, so the ELF is created as "outdefault.elf"
# in BUILD_DIR instead of "out/default.elf".
#
# Symptom without this file:
#   [152/152] Convert built file to .hex
#   FAILED: ... default.elf: No such file
#
# Fix:
#   1. Set RUNTIME_OUTPUT_DIRECTORY to BUILD_DIR/out so that Ninja writes the
#      short relative path "out\default.elf".  The mangled output is then
#      always the predictable "outdefault.elf" in BUILD_DIR.
#   2. Create BUILD_DIR/out/ at CMake configure time so it is ready for bin2hex.
#   3. POST_BUILD: copy "outdefault.elf" to:
#        a) BUILD_DIR/out/default.elf  — keeps Ninja's dep-tracking correct
#           (incremental builds skip the link step when this file is current).
#        b) canonical out/tcpip_iperf_lan865x/ — where xc32-bin2hex, flash.py
#           and build_summary.py all expect to find the ELF/HEX.

file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/out")

set_target_properties(tcpip_iperf_lan865x_default_image_fZZHodlt PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/out")

add_custom_command(TARGET tcpip_iperf_lan865x_default_image_fZZHodlt POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E make_directory
        "${tcpip_iperf_lan865x_default_output_dir}"
    # a) satisfy Ninja's declared output so incremental builds don't always relink
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${CMAKE_BINARY_DIR}/outdefault.elf"
        "${CMAKE_BINARY_DIR}/out/default.elf"
    # b) canonical location for xc32-bin2hex, flash.py, build_summary.py
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${CMAKE_BINARY_DIR}/outdefault.elf"
        "${tcpip_iperf_lan865x_default_output_dir}/default.elf"
    COMMENT "Normalizing ELF location (xc32 MINGW backslash workaround)"
    VERBATIM)
