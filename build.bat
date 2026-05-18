@echo off
REM Run from the folder containing main.py, magnifier.py, Config.py

pyinstaller ^
    --onefile ^
    --noconsole ^
    --name "FPSMagnifier" ^
    --hidden-import win32gui ^
    --hidden-import win32con ^
    --hidden-import win32api ^
    --hidden-import pywintypes ^
    --hidden-import OpenGL.GL ^
    --hidden-import OpenGL.WGL ^
    --hidden-import OpenGL.platform.win32 ^
    --hidden-import OpenGL.arrays.ctypesarrays ^
    --hidden-import OpenGL.arrays.ctypesparameters ^
    --hidden-import OpenGL.arrays.ctypespointers ^
    --hidden-import OpenGL.arrays.strings ^
    --hidden-import OpenGL.arrays.numbers ^
    --hidden-import OpenGL.arrays.numpymodule ^
    --collect-all OpenGL ^
    main.py

echo.
echo ─── Done! Find your exe at: dist\FPSMagnifier.exe ───
pause