@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "CMAKE_DIR=%SCRIPT_DIR%cmake\tcpip_iperf_lan865x\default"
pushd "%SCRIPT_DIR%..\..\..\..\..\"
set "BUILD_DIR=%CD%\temp\tcpip_iperf_lan865x\default"
popd

:: ---------------------------------------------------------------------------
:: Read compiler selection from setup_compiler.config (written by setup_compiler.py)
:: Uses PowerShell to parse the JSON — no extra tools required.
:: ---------------------------------------------------------------------------
set "COMPILER_CONFIG=%SCRIPT_DIR%setup_compiler.config"
if not exist "%COMPILER_CONFIG%" (
    echo ERROR: No compiler configured.
    echo        Run "python setup_compiler.py" first to select an XC32 version.
    exit /b 1
)

:: Extract fields from JSON via PowerShell
for /f "usebackq delims=" %%V in (
    `powershell -NoProfile -Command "(Get-Content '%COMPILER_CONFIG%' | ConvertFrom-Json).version"`
) do set "XC32_VERSION=%%V"

for /f "usebackq delims=" %%P in (
    `powershell -NoProfile -Command "(Get-Content '%COMPILER_CONFIG%' | ConvertFrom-Json).compiler"`
) do set "XC32_COMPILER=%%P"

for /f "usebackq delims=" %%D in (
    `powershell -NoProfile -Command "(Get-Content '%COMPILER_CONFIG%' | ConvertFrom-Json).bin_dir"`
) do set "XC32_BIN_DIR=%%D"

:: Verify the compiler binary is present
if not exist "%XC32_COMPILER%" (
    echo ERROR: Selected compiler not found: %XC32_COMPILER%
    echo        XC32 %XC32_VERSION% does not appear to be installed on this machine.
    echo        Run "python setup_compiler.py" to select an installed version.
    exit /b 1
)

echo Compiler  : XC32 %XC32_VERSION%  (%XC32_COMPILER%)

:: ---------------------------------------------------------------------------
:: Parse build mode parameter
:: ---------------------------------------------------------------------------
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
cmake --preset tcpip_iperf_lan865x_default_conf -S "%CMAKE_DIR%" -B "%BUILD_DIR%" -DPACK_REPO_PATH="%USERPROFILE%/.mchp_packs" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
if errorlevel 1 (
    echo ERROR: CMake configure failed.
    exit /b 1
)

:: --------------------------------------------------------------------------
:: Copy compile_commands.json to the repo root so VSCode's C/C++ extension
:: (configured via .vscode/c_cpp_properties.json) can resolve "Find All
:: References" / "Go to Definition" across the project.
:: SCRIPT_DIR is .../net_10base_t1s/apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/
:: so ..\..\..\.. climbs up to the repo root (net_10base_t1s/).
:: --------------------------------------------------------------------------
if exist "%BUILD_DIR%\compile_commands.json" (
    pushd "%SCRIPT_DIR%..\..\..\.." >nul
    copy /Y "%BUILD_DIR%\compile_commands.json" "compile_commands.json" >nul
    echo compile_commands.json copied to repo root for VSCode IntelliSense.
    popd >nul
)

echo [2/2] Building with Ninja...
cmake --build "%BUILD_DIR%"
if errorlevel 1 (
    echo ERROR: Build failed.
    exit /b 1
)

echo BUILD SUCCESSFUL.

:: ---------------------------------------------------------------------------
:: Post-build summary: flash/RAM usage, heap, active interrupts
:: ---------------------------------------------------------------------------
set "ELF_PATH=%SCRIPT_DIR%out\tcpip_iperf_lan865x\default.elf"
if exist "%ELF_PATH%" (
    python "%SCRIPT_DIR%build_summary.py" "%BUILD_DIR%" "%ELF_PATH%" "%XC32_BIN_DIR%"
) else (
    echo WARNING: ELF not found, skipping build summary.
)
endlocal
