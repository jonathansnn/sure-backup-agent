#
# Cria a Tarefa Agendada "Sure Backup Agent - Daily Report" no Task Scheduler.
# Roda diariamente as 08:00 sob o usuario corrente.
#
# Rode na VM-com-Veeam APOS validar smoke E2E:
#   .\install_task.ps1
#
# Pra desinstalar/recriar:
#   Unregister-ScheduledTask -TaskName "Sure Backup Agent - Daily Report" -Confirm:$false
#

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$batPath = Join-Path $root "run_daily.bat"

# Le o modo do config.toml. Modo TIM roda 5min antes do agregador pra garantir
# que o artefato esta no shared_dir quando V+P for ler.
$venvPython = "$root\.venv\Scripts\python.exe"
$modeName = & $venvPython -c "import tomllib; print(tomllib.load(open(r'$root\config.toml','rb')).get('mode',{}).get('name','all'))"

switch ($modeName) {
    "timeismoney" {
        $taskName  = "Sure Backup Agent - TIM Producer"
        $dailyTime = "07:55"
    }
    "ppdm" {
        $taskName  = "Sure Backup Agent - PPDM Producer"
        $dailyTime = "07:55"
    }
    "veeam" {
        $taskName  = "Sure Backup Agent - Veeam Aggregator"
        $dailyTime = "08:00"
    }
    "veeam_ppdm" {
        $taskName  = "Sure Backup Agent - Veeam+PPDM Aggregator"
        $dailyTime = "08:00"
    }
    default {
        $taskName  = "Sure Backup Agent - Daily Report"
        $dailyTime = "08:00"
    }
}

Write-Host "=== Instalando Task Scheduler ===" -ForegroundColor Cyan
Write-Host "Tarefa:    $taskName"
Write-Host "Bat:       $batPath"
Write-Host "Horario:   diario as $dailyTime"
Write-Host "Usuario:   $env:USERDOMAIN\$env:USERNAME (interactive)"
Write-Host ""

if (-not (Test-Path $batPath)) {
    Write-Error "Nao encontrei $batPath. Rode esse script da raiz do projeto."
}

# Remover task existente, se houver
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Tarefa ja existe, removendo pra recriar..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Action: rodar o .bat
$action = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $root

# Trigger: diario as 08:00
$trigger = New-ScheduledTaskTrigger -Daily -At $dailyTime

# Principal: rodar como usuario corrente, com perfil interativo
# (necessario porque Veeam GUI precisa de sessao Windows ativa)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

# Settings: nao iniciar se em bateria, retry 3x se falhar
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 0 `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $taskName `
    -Description "Captura status diario de Veeam + PPDM e posta no Teams da diretoria. Codigo em $root" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings | Out-Null

Write-Host ""
Write-Host "=== Tarefa criada com sucesso ===" -ForegroundColor Green
Write-Host ""
Write-Host "Proximos passos:"
Write-Host "  1. Validar execucao manual:"
Write-Host "       Start-ScheduledTask -TaskName ""$taskName"""
Write-Host "     Aguarda 30s, depois verifica:"
Write-Host "       Get-ScheduledTaskInfo -TaskName ""$taskName"" | Format-List LastRunTime, LastTaskResult"
Write-Host "     LastTaskResult=0 significa sucesso."
Write-Host ""
Write-Host "  2. Ver no GUI: Iniciar -> Agendador de Tarefas -> Biblioteca -> $taskName"
Write-Host ""
Write-Host "  3. Logs do agente: $root\logs\agent.log"
