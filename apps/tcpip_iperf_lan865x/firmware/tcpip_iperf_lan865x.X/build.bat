@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "CMAKE_DIR=%SCRIPT_DIR%cmake\tcpip_iperf_lan865x\default"
set "BUILD_DIR=%SCRIPT_DIR%_build\tcpip_iperf_lan865x\default"

echo [1/2] Configuring with CMake...
cmake --preset tcpip_iperf_lan865x_default_conf --fresh -S "%CMAKE_DIR%" -B "%BUILD_DIR%"
if errorlevel 1 (
    echo ERROR: CMake configure failed.
    exit /b 1
)

echo [2/2] Building with Ninja...
cmake --build "%BUILD_DIR%"
if errorlevel 1 (
    echo ERROR: Build failed.
    exit /b 1
)

echo BUILD SUCCESSFUL.
endlocal
