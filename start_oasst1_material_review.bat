@echo off
set "PROJECT_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start_oasst1_material_review.ps1"
