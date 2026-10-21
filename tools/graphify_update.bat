@echo off
setlocal enabledelayedexpansion
REM ===========================================================================
REM graphify_update.bat  --  TEMPLATE
REM
REM Manual, no-AI live-graph refresh + smoke test. Copy this into your
REM project's  tools\  folder and adjust CODE_DIR below to your source folder
REM (lib / src / app / internal ...). Run it after code changes or to check
REM graphify works.
REM
REM What it does (no LLM, no API key, no token cost):
REM   Code-only AST refresh of the LIVE root graphify-out\graph.json via
REM         `graphify update`. GRAPHIFY_OUT is pointed at the root folder and
REM         CODE_DIR is passed absolute -- both required, otherwise the CLI
REM         writes a stray graph under CODE_DIR\graphify-out\ or re-anchors
REM         every node id. Directed flag + community labels are kept.
REM         Docs are not re-extracted; a FIRST build is the skill flow
REM              /graphify src addon --directed
REM         inside Claude -- never delete graph.json before running this bat.
REM   Smoke test -- confirm the live root graph exists, is directed, and
REM         answers a query. This is the "does graphify actually work here" check.
REM ===========================================================================

REM ---- adjust this to your project's code folder -------------------------------
set "CODE_DIR=src"
set "ADDON_CODE_DIR=addon"
REM -----------------------------------------------------------------------------

REM This bat lives in tools\ ; run everything from the repo root so graphify
REM resolves the live graph at .\graphify-out\ (relative to cwd).
pushd "%~dp0.."

REM Resolve the graphify launcher: prefer PATH, else derive from the interpreter
REM the last build saved (graphify.exe sits next to that python.exe).
set "GRAPHIFY=graphify"
where graphify >nul 2>nul
if errorlevel 1 (
  if exist "graphify-out\.graphify_python" (
    set /p GPY=<graphify-out\.graphify_python
    set "GRAPHIFY=!GPY:python.exe=graphify.exe!"
  ) else (
    echo ERROR: graphify not on PATH and no graphify-out\.graphify_python found.
    echo Build the graph once in Claude:  /graphify src addon --directed
    popd & endlocal & exit /b 1
  )
)

echo === live-graph AST refresh (no LLM) : %CODE_DIR% and %ADDON_CODE_DIR% ===
set "GRAPHIFY_OUT=%CD%\graphify-out"
"%GRAPHIFY%" update "%CD%\%CODE_DIR%"
if errorlevel 1 (popd & endlocal & exit /b 1)
"%GRAPHIFY%" update "%CD%\%ADDON_CODE_DIR%"
if errorlevel 1 (popd & endlocal & exit /b 1)
echo.

echo === [2/2] smoke test: live root graph ===
if not exist "graphify-out\graph.json" (
  echo ERROR: graphify-out\graph.json not found.
  echo Build it in Claude:  /graphify src addon --directed
  popd & endlocal & exit /b 1
)
REM Validate the live root graph; printing its shape alone is not a smoke check.
if not exist "graphify-out\.graphify_python" (
  echo ERROR: graphify-out\.graphify_python not found; cannot validate graph shape.
  popd & endlocal & exit /b 1
)
set /p GPY2=<graphify-out\.graphify_python
if not defined GPY2 (
  echo ERROR: graphify-out\.graphify_python is empty; cannot validate graph shape.
  popd & endlocal & exit /b 1
)
"!GPY2!" -c "import json,sys;d=json.load(open('graphify-out/graph.json',encoding='utf-8'));n=d.get('nodes');print('directed:',d.get('directed'),'  nodes:',len(n) if isinstance(n,list) else 'invalid');sys.exit(0 if d.get('directed') is True and isinstance(n,list) and len(n)>0 else 1)"
if errorlevel 1 (
  echo ERROR: live graph must be directed and contain at least one node.
  popd & endlocal & exit /b 1
)
echo.
echo --- god nodes (top 5) ---
"%GRAPHIFY%" god-nodes --top 5
echo.
echo --- sample query ---
"%GRAPHIFY%" query "What are the main modules and how do they connect?"
set "QUERY_RESULT=%ERRORLEVEL%"
popd
endlocal & exit /b %QUERY_RESULT%
