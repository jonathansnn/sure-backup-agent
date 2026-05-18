@echo off
REM Entry point pro Task Scheduler. Roda o sure-backup-agent uma vez.
REM Saida vai pro logger Python (logs\agent.log); aqui so capturamos
REM eventuais erros do proprio venv/python.

cd /d C:\sure-backup-agent

REM PYTHONIOENCODING=utf-8 garante que prints com acentos nao quebrem
REM em ambientes com codepage cp1252 (Windows pt-BR padrao).
set PYTHONIOENCODING=utf-8

.\.venv\Scripts\python.exe -m src.main
exit /b %ERRORLEVEL%
