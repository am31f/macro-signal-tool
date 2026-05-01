@echo off
title MacroSignalTool
echo.
echo  ╔══════════════════════════════════════╗
echo  ║      MacroSignalTool — Avvio         ║
echo  ╚══════════════════════════════════════╝
echo.

REM ── Backend (FastAPI in finestra separata) ──────────────────────────────────
start "MacroSignalTool — Backend" cmd /k "cd /d "%~dp0files" && echo [BACKEND] Avvio uvicorn su http://localhost:8000 ... && uvicorn main:app --reload"

REM Attendi 3 secondi che il backend si avvii prima del frontend
timeout /t 3 /nobreak >nul

REM ── Frontend (Vite in finestra separata) ────────────────────────────────────
start "MacroSignalTool — Frontend" cmd /k "cd /d "%~dp0frontend" && echo [FRONTEND] Avvio Vite su http://localhost:3000 ... && npm run dev"

REM Attendi 4 secondi che Vite compili
timeout /t 4 /nobreak >nul

REM ── Apri il browser ─────────────────────────────────────────────────────────
echo  Apertura browser su http://localhost:3000 ...
start "" "http://localhost:3000"

echo.
echo  Entrambi i servizi sono avviati.
echo  Chiudi le due finestre terminale per fermarli.
echo.
pause
