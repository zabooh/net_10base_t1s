@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "CMAKE_DIR=%SCRIPT_DIR%cmake\tcpip_iperf_lan865x\default"
pushd "%SCRIPT_DIR%..\..\..\..\..\"
set "BUILD_DIR=%CD%\temp\tcpip_iperf_lan865x\default"
popd

set "MODE=incremental"
if not "%~1"=="" set "MODE=%~1"

if /i "%MODE%"=="help" goto :help
if /i "%MODE%"=="clean" goto :clean
if /i "%MODE%"=="rebuild" goto :rebuild
if /i "%MODE%"=="incremental" goto :incremental

echo ERROR: Unknown parameter "%~1"
goto :help

:help
echo Usage: build.bat [incremental^|clean^|rebuild^|help]
echo   (no argument)  Incremental build (default)
echo   incremental    Incremental build - only recompiles changed files
echo   clean          Delete all temporary build artifacts
echo   rebuild        Clean then perform a full build
echo   help           Show this help
exit /b 0

:clean
echo Cleaning build directory...
if exist "%BUILD_DIR%" (
    rmdir /s /q "%BUILD_DIR%"
    echo Deleted: %BUILD_DIR%
) else (
    echo Nothing to clean.
)
exit /b 0

:rebuild
echo [0/2] Cleaning before rebuild...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
goto :build

:incremental
:build
echo [1/2] Configuring with CMake...
cmake --preset tcpip_iperf_lan865x_default_conf -S "%CMAKE_DIR%" -B "%BUILD_DIR%"
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
