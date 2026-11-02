@echo off
setlocal enabledelayedexpansion

set ADDON_NAME=typix
set DIST_DIR=dist
set STAGE_DIR=build_stage

rem Auto-increment version in manifest.ini
echo Incrementing version...
python tools\bump_version.py

rem Read version from manifest.ini
set VERSION=
for /f "tokens=1,* delims==" %%A in ('findstr /i "^version" manifest.ini') do (
    set "VERSION=%%B"
)
rem Trim whitespace
for /f "tokens=* delims= " %%V in ("!VERSION!") do set "VERSION=%%V"

if "!VERSION!"=="" (
    echo Error: Could not read version from manifest.ini
    exit /b 1
)

echo Building NVDA add-on: %ADDON_NAME% v!VERSION!

if not exist manifest.ini (
    echo Error: manifest.ini not found in current directory.
    exit /b 1
)

if exist %DIST_DIR% rd /s /q %DIST_DIR%
if exist %STAGE_DIR% rd /s /q %STAGE_DIR%

mkdir %DIST_DIR%
mkdir %STAGE_DIR%

rem Convert markdown documentation to HTML
if exist doc (
    echo Converting documentation...
    python tools\md2html.py
)

echo Copying files to build stage...
copy manifest.ini %STAGE_DIR% /y

rem Core plugin code and soundpacks (skip __pycache__/.pyc cruft from local test runs)
xcopy globalPlugins %STAGE_DIR%\globalPlugins /E /I /Y /EXCLUDE:tools\build_exclude.txt

rem Documentation (HTML and CSS only, no .md source)
if exist doc (
    xcopy doc %STAGE_DIR%\doc /E /I /Y /EXCLUDE:tools\build_exclude.txt
)

rem Translations: compile each nvda.po to nvda.mo and write its manifest.ini
python tools\i18n.py build %STAGE_DIR%
if errorlevel 1 (
    echo Error: translations failed to build. Is Babel installed? pip install babel
    exit /b 1
)

echo Creating .nvda-addon package...
powershell -Command "Compress-Archive -Path '%STAGE_DIR%\*' -DestinationPath '%DIST_DIR%\%ADDON_NAME%-!VERSION!.zip' -Force"

move %DIST_DIR%\%ADDON_NAME%-!VERSION!.zip %DIST_DIR%\%ADDON_NAME%-!VERSION!.nvda-addon

echo Done!
echo Package is available at: %DIST_DIR%\%ADDON_NAME%-!VERSION!.nvda-addon

rd /s /q %STAGE_DIR%

endlocal
