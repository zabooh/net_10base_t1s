@echo off
REM ---------------------------------------------------------------------------
REM Build target: regenerate the ACMA Use-Cases PowerPoint deck.
REM
REM Pipeline:
REM   1. Run extract_microchip_diagrams.py (if diagrams not yet extracted)
REM      -- pulls relevant ACMA / PTP / TSU images out of the Microchip
REM         template PPTX files in documentation/pdf/.
REM   2. Run generate_acma_use_cases_pptx.py
REM      -- builds documentation/pdf/acma_use_cases.pptx using the
REM         Microchip slide-master template (preserves branding) plus the
REM         extracted diagrams plus our custom use-case content.
REM
REM Requires:
REM   - Python 3 with python-pptx >= 1.0
REM   - The two Microchip PPTX templates already present in
REM     documentation/pdf/:
REM       3_Special Features of LAN8670_1_2 LAN8650_1.pptx
REM       LAN867x_PHY_d47.pptx
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"

REM Verify python-pptx is available
python -c "import pptx" >nul 2>&1
if errorlevel 1 (
    echo ERROR: python-pptx is not installed.
    echo        Install with: pip install python-pptx
    exit /b 1
)

REM Step 1 -- extract diagrams (the generator does this automatically if
REM the _extracted_diagrams folder is empty, but we run it explicitly
REM here so a missing or relocated image folder is regenerated).
echo [1/2] Extracting Microchip ACMA / PTP / TSU diagrams...
python "%SCRIPT_DIR%documentation\ptp\extract_microchip_diagrams.py"
if errorlevel 1 (
    echo ERROR: Diagram extraction failed.
    exit /b 1
)

REM Step 2 -- generate the deck
echo [2/2] Generating ACMA Use-Cases PowerPoint...
python "%SCRIPT_DIR%documentation\ptp\generate_acma_use_cases_pptx.py"
if errorlevel 1 (
    echo ERROR: PPTX generation failed.
    exit /b 1
)

echo.
echo BUILD SUCCESSFUL.
echo Output: %SCRIPT_DIR%documentation\pdf\acma_use_cases.pptx
endlocal
