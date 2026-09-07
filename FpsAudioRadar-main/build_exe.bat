@echo off
title FPS Audio Radar - Build EXE

echo ============================================
echo   FPS Audio Radar - Build EXE
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.9+.
    pause
    exit /b 1
)

echo [1/2] Installing PyInstaller...
pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed.
    pause
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "FPS_Audio_Radar.spec" del /q "FPS_Audio_Radar.spec"

echo.
echo [2/2] Building EXE (may take 1-3 minutes)...
echo.

pyinstaller --onefile --windowed --noconfirm --name FPS_Audio_Radar --hidden-import=pyaudiowpatch --collect-submodules=pyaudiowpatch fps_audio_radar.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Please screenshot the error above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build complete!
echo   EXE: dist\FPS_Audio_Radar.exe
echo ============================================
echo.
pause
