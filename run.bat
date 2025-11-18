@echo off
REM === ACTIVAR ENTORNO VIRTUAL Y LEVANTAR FLASK ===

REM Ir a la carpeta del proyecto
cd /d "%~dp0"

REM Activar entorno virtual
call venv\Scripts\activate.bat

REM Ejecutar la app
python app.py

REM Mantener la ventana abierta para ver errores
pause
