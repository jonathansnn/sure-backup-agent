@echo off
REM Entry point pro Task Scheduler. Roda o sure-backup-agent uma vez.
REM Saida vai pro logger Python (logs\agent.log); aqui so capturamos
REM eventuais erros do proprio venv/python.

REM %~dp0 = pasta deste .bat (com barra final). Torna o script portatil:
REM funciona em qualquer checkout (ex: C:\sure-backup-agent e
REM C:\sure-backup-agent-ppdm no deploy full-split na mesma maquina).
cd /d "%~dp0"

REM PYTHONIOENCODING=utf-8 garante que prints com acentos nao quebrem
REM em ambientes com codepage cp1252 (Windows pt-BR padrao).
set PYTHONIOENCODING=utf-8

REM (Workaround vmxnet3 comentado — provavelmente desnecessario apos adicionar
REM  --ignore-certificate-errors no Chromium do PPDM. Se a captura PPDM voltar
REM  a dar timeout esporadicamente, descomenta as 2 linhas abaixo.)
REM powershell -NoProfile -Command "Restart-NetAdapter -Name 'VL20' -Confirm:$false"
REM timeout /t 5 /nobreak >nul

.\.venv\Scripts\python.exe -m src.main
exit /b %ERRORLEVEL%
