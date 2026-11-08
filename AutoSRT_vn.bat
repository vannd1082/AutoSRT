@echo off

chcp 65001 >nul

setlocal EnableDelayedExpansion

cd /d %~dp0

title MFA AUTO ALIGN + SRT BUILD (VIETNAMESE)

echo ==========================================
echo   MFA AUTO ALIGN + SRT BUILD - VIETNAMESE
echo ==========================================
echo.

REM =====================================================
REM Vietnamese MFA model settings
REM   Dictionary : vietnamese_ho_chi_minh_city_mfa
REM   Acoustic   : vietnamese_mfa
REM   G2P model  : vietnamese_ho_chi_minh_city_mfa
REM
REM If your voiceover accent is Northern Vietnamese instead
REM of Southern, change all three lines below to the Hanoi
REM versions instead:
REM   vietnamese_hanoi_mfa
REM =====================================================

set "DICT_NAME=vietnamese_ho_chi_minh_city_mfa"
set "ACOUSTIC_NAME=vietnamese_mfa"
set "G2P_NAME=vietnamese_ho_chi_minh_city_mfa"


REM =====================================================
REM Find the first audio file (prefer WAV, otherwise use MP3)
REM =====================================================

set "AUDIO="
set "EXT="

for %%F in (*.wav) do (
    set "AUDIO=%%~nF"
    set "EXT=wav"
    goto :FOUND_AUDIO
)

for %%F in (*.mp3) do (
    set "AUDIO=%%~nF"
    set "EXT=mp3"
    goto :FOUND_AUDIO
)

:FOUND_AUDIO

if not defined AUDIO (
    echo ERROR: No WAV or MP3 file found.
    goto :EXIT
)

set "WAV=%AUDIO%"

echo Audio : %AUDIO%.%EXT%
echo.



REM =====================================================
REM Find the first TXT file
REM =====================================================

set "TXT="

for %%F in (*.txt) do (
    set "TXT=%%F"
    goto :FOUND_TXT
)

:FOUND_TXT

if not defined TXT (
    echo ERROR: No TXT file found.
    goto :EXIT
)

echo Script: %TXT%
echo.



REM =====================================================
REM Find the first PY file
REM =====================================================

set "PYSCRIPT="

for %%F in (*.py) do (
    set "PYSCRIPT=%%F"
    goto :FOUND_PY
)

:FOUND_PY

if not defined PYSCRIPT (
    echo ERROR: No PY file found.
    goto :EXIT
)

echo Script (Python): %PYSCRIPT%
echo.



REM =====================================================
REM Activate Miniforge (only once)
REM =====================================================

call C:\Users\vannd\miniforge3\Scripts\activate.bat

if errorlevel 1 (
    echo ERROR: Failed to activate Miniforge.
    goto :EXIT
)



REM =====================================================
REM If the input is MP3, convert it to 16kHz mono WAV before running MFA
REM =====================================================

if /I "%EXT%"=="mp3" (
    echo MP3 file detected - converting to 16kHz mono WAV...
    where ffmpeg >nul 2>&1
    if errorlevel 1 (
        echo ERROR: ffmpeg was not found in PATH. Please install ffmpeg or check your Miniforge environment.
        goto :EXIT
    )
    ffmpeg -y -i "%AUDIO%.mp3" -ac 1 -ar 16000 "%AUDIO%.wav" >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Failed to convert MP3 to WAV.
        goto :EXIT
    )
    echo Created: %AUDIO%.wav
    echo.
)



REM =====================================================
REM Skip MFA if a TextGrid already exists
REM =====================================================

if exist "%WAV%.TextGrid" (
    echo Existing TextGrid found.
    echo Skipping MFA.
    echo.
    goto :BUILD_SRT
)



REM =====================================================
REM Create LAB file
REM =====================================================

copy /Y "%TXT%" "%WAV%.lab" >nul

if errorlevel 1 (
    echo ERROR: Failed to create %WAV%.lab
    goto :EXIT
)

echo Created: %WAV%.lab
echo.



REM =====================================================
REM Generate pronunciations for OOV words (mfa g2p) and add
REM them directly into the named dictionary (mfa model
REM add_words). This is the official MFA workflow and does
REM NOT require manually merging dictionary files.
REM
REM Vietnamese Dharma/Buddhist scripts often contain Han-Viet
REM terms, proper names, or Pali transliterations that are
REM NOT in the base MFA Vietnamese dictionary (~7,500 words),
REM so this step is important, not optional.
REM =====================================================

set "OOV_DICT=g2pped_oovs.dict"

if exist "%OOV_DICT%" del /Q "%OOV_DICT%" >nul 2>&1

echo Generating pronunciations for any out-of-vocabulary words...
echo.

mfa g2p "%CD%" %G2P_NAME% "%CD%\%OOV_DICT%" --dictionary_path %DICT_NAME% --clean --single_speaker

if errorlevel 1 (
    echo.
    echo ERROR: mfa g2p failed. See the message above for details.
    goto :EXIT
)

if exist "%OOV_DICT%" (

    for %%A in ("%OOV_DICT%") do set "OOV_SIZE=%%~zA"

    if not "!OOV_SIZE!"=="0" (

        echo OOV words found - adding new pronunciations to %DICT_NAME%...
        echo.

        mfa model add_words %DICT_NAME% "%CD%\%OOV_DICT%"

        if errorlevel 1 (
            echo.
            echo ERROR: mfa model add_words failed.
            goto :EXIT
        )

        echo New words added to dictionary: %DICT_NAME%
        echo.

    ) else (

        echo No OOV words found. Dictionary already covers this script.
        echo.

    )

) else (

    echo No OOV words found. Dictionary already covers this script.
    echo.

)



REM =====================================================
REM Run MFA
REM =====================================================

echo Running MFA (Vietnamese)...
echo   Dictionary : %DICT_NAME%
echo   Acoustic   : %ACOUSTIC_NAME%
echo.

mfa align "%CD%" %DICT_NAME% %ACOUSTIC_NAME% "%CD%" --clean

if errorlevel 1 (
    echo.
    echo ERROR: MFA failed.
    goto :EXIT
)

if not exist "%WAV%.TextGrid" (
    echo.
    echo ERROR: Failed to create %WAV%.TextGrid
    goto :EXIT
)

echo.
echo TextGrid created: %WAV%.TextGrid
echo.



REM =====================================================
REM Build SRT
REM =====================================================

:BUILD_SRT

if not exist "%TXT%" (
    echo ERROR: TXT file not found.
    goto :EXIT
)

if not exist "%WAV%.TextGrid" (
    echo ERROR: TextGrid file not found.
    goto :EXIT
)

echo Building SRT...
echo.

python "%CD%\%PYSCRIPT%" "%TXT%" "%WAV%.TextGrid" "%WAV%.srt"

if errorlevel 1 (
    echo.
    echo ERROR: smart_srt.py failed.
    goto :EXIT
)

if exist "%WAV%.srt" (
    echo.
    echo ==========================================
    echo SUCCESS!
    echo Created: %WAV%.srt
    echo ==========================================
) else (
    echo.
    echo ERROR: Failed to create SRT.
)



:EXIT

echo.
echo Press any key to exit...

pause >nul
