@ECHO off
SETLOCAL
CD /D "%~dp0painel"
IF NOT EXIST "target\release\painel.exe" (
  ECHO Painel nao encontrado. Compile com: cargo build --release
  EXIT /B 1
)
START "Painel Claude + Codex" "%CD%\target\release\painel.exe"
