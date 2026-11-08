@echo off

chcp 65001 >nul

setlocal EnableDelayedExpansion

cd /d %~dp0

title MFA AUTO ALIGN + SRT BUILD

echo ==========================================
echo        MFA AUTO ALIGN + SRT BUILD
echo ==========================================
echo.



REM =====================================================
REM Folder structure expected by smart_srt_v4_2.py:
REM
REM   %~dp0input\   -> put script .txt + audio .mp3/.wav here
REM   %~dp0output\  -> .TextGrid and .srt will be created here
REM
REM smart_srt_v4_2.py auto-detects the files inside "input\"
REM and auto-creates "input\" / "output\" if missing.
REM =====================================================

if not exist "%~dp0input" (
    mkdir "%~dp0input"
    echo Created folder: input
)

if not exist "%~dp0output" (
    mkdir "%~dp0output"
    echo Created folder: output
)

echo Input folder : %~dp0input
echo Output folder: %~dp0output
echo.



REM =====================================================
REM Find the Python pipeline script (smart_srt_v4_2.py)
REM Looks in the same folder as this .bat file.
REM =====================================================

set "PYSCRIPT="

if exist "%~dp0smart_srt_v4_2.py" (
    set "PYSCRIPT=%~dp0smart_srt_v4_2.py"
) else (
    for %%F in ("%~dp0smart_srt*.py") do (
        set "PYSCRIPT=%%~fF"
        goto :FOUND_PY
    )
)

:FOUND_PY

if not defined PYSCRIPT (
    echo ERROR: smart_srt_v4_2.py not found next to this .bat file.
    goto :EXIT
)

echo Script (Python): %PYSCRIPT%
echo.



REM =====================================================
REM Quick pre-check: make sure input\ has what the
REM pipeline needs, so failures are obvious immediately
REM instead of after MFA has already started.
REM =====================================================

set "FOUND_TXT="
set "FOUND_AUDIO="

for %%F in ("%~dp0input\*.txt") do set "FOUND_TXT=1"
for %%F in ("%~dp0input\*.mp3") do set "FOUND_AUDIO=1"
for %%F in ("%~dp0input\*.wav") do set "FOUND_AUDIO=1"

if not defined FOUND_TXT (
    echo ERROR: No .txt script found in "input\" folder.
    goto :EXIT
)

if not defined FOUND_AUDIO (
    echo ERROR: No .mp3 or .wav audio found in "input\" folder.
    goto :EXIT
)



REM =====================================================
REM Activate Miniforge
REM =====================================================

call C:\Users\vannd\miniforge3\Scripts\activate.bat

if errorlevel 1 (
    echo ERROR: Failed to activate Miniforge.
    goto :EXIT
)



REM =====================================================
REM Run the full pipeline.
REM smart_srt_v4_2.py automatically:
REM   1. detects .txt + .mp3/.wav inside input\
REM   2. detects language
REM   3. converts MP3 -> WAV (if needed)
REM   4. runs MFA alignment
REM   5. builds output\<name>.TextGrid and output\<name>.srt
REM =====================================================

echo Running pipeline...
echo.

python "%PYSCRIPT%"

if errorlevel 1 (
    echo.
    echo ERROR: smart_srt_v4_2.py failed.
    goto :EXIT
)

echo.
echo ==========================================
echo SUCCESS!
echo Check the "output" folder for .TextGrid and .srt files.
echo ==========================================



:EXIT

echo.
echo Press any key to exit...

pause >nul
