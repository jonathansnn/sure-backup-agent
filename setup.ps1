# Bootstrap do sure-backup-agent numa maquina nova.
# Rode da raiz do projeto: .\setup.ps1
# Pre-requisito: Python 3.11+ instalado e no PATH.
#
# NOTA: arquivo em ASCII puro (sem acentos nem em-dash) por compatibilidade
# com PowerShell 5.1 que le arquivos sem BOM como Windows-1252.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Write-Host "=== sure-backup-agent setup ===" -ForegroundColor Cyan
Write-Host "Diretorio do projeto: $root"

# 1. Verificar Python
Write-Host "`n[1/5] Verificando Python..."
$pyVer = (python --version 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python nao encontrado no PATH. Instale Python 3.11+ primeiro."
}
Write-Host "  $pyVer" -ForegroundColor Green

# 2. Criar venv se nao existir
Write-Host "`n[2/5] Configurando venv em .venv/"
if (Test-Path "$root\.venv") {
    Write-Host "  .venv ja existe, pulando criacao" -ForegroundColor Yellow
} else {
    python -m venv "$root\.venv"
    Write-Host "  .venv criado" -ForegroundColor Green
}

$venvPython = "$root\.venv\Scripts\python.exe"

# 3. Instalar dependencias
Write-Host "`n[3/5] Instalando dependencias do requirements.txt (pode demorar)..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r "$root\requirements.txt" --quiet
Write-Host "  Dependencias instaladas" -ForegroundColor Green

# 4. Instalar Chromium do Playwright (~111 MB)
Write-Host "`n[4/5] Baixando Chromium do Playwright (~111 MB, demora)..."
& $venvPython -m playwright install chromium
Write-Host "  Chromium instalado" -ForegroundColor Green

# 5. Configurar secrets no Windows Credential Manager
Write-Host "`n[5/5] Configurando secrets no Windows Credential Manager"

# Le o modo do config.toml pra saber quais secrets sao relevantes nessa VM
$modeName = & $venvPython -c "import tomllib; print(tomllib.load(open(r'$root\config.toml','rb')).get('mode',{}).get('name','all'))"
Write-Host "  Modo desta VM: $modeName" -ForegroundColor Cyan
Write-Host "    all          -> webhook 'Send Daily Full'   + senha PPDM + senha TIM"
Write-Host "    veeam_ppdm   -> webhook 'Aggregate + Send'  + senha PPDM"
Write-Host "    timeismoney  -> webhook 'Store TIM Artifact'+ senha TIM"
Write-Host ""
Write-Host "  IMPORTANTE: a URL do webhook desta VM deve apontar pro fluxo PA correto"
Write-Host "  do modo acima. Mesmo NOME de secret em todas as VMs (teams_webhook),"
Write-Host "  VALOR diferente apontando pro fluxo certo de cada uma." -ForegroundColor Yellow
Write-Host ""

$needsWebhook = $true
$needsPpdm    = ($modeName -eq "all" -or $modeName -eq "veeam_ppdm")
$needsTim     = ($modeName -eq "all" -or $modeName -eq "timeismoney")

# 5a. Webhook URL (so se preciso)
if ($needsWebhook) {
    $existingWebhook = & $venvPython -c "import keyring; v = keyring.get_password('sure-backup-agent/teams_webhook', 'url'); print('OK' if v else 'MISSING')"
    if ($existingWebhook.Trim() -eq "OK") {
        Write-Host "  Webhook URL ja configurada - pulando" -ForegroundColor Yellow
    } else {
        $webhookUrl = Read-Host -Prompt "  Cole aqui a URL HTTP do trigger do Power Automate"
        if ([string]::IsNullOrWhiteSpace($webhookUrl)) {
            Write-Host "  URL vazia - pulando (configure depois com: python -m keyring set sure-backup-agent/teams_webhook url)" -ForegroundColor Yellow
        } else {
            $env:WEBHOOK_TMP = $webhookUrl
            & $venvPython -c "import os, keyring; keyring.set_password('sure-backup-agent/teams_webhook', 'url', os.environ['WEBHOOK_TMP'])"
            Remove-Item Env:\WEBHOOK_TMP
            Write-Host "  Webhook URL salva" -ForegroundColor Green
        }
    }
} else {
    Write-Host "  Webhook URL nao exigida pelo modo '$modeName' - pulando" -ForegroundColor DarkGray
}

# 5b. Senha PPDM (so se preciso)
if ($needsPpdm) {
    $ppdmUsername = & $venvPython -c "import tomllib; print(tomllib.load(open(r'$root\config.toml','rb'))['ppdm']['username'])"
    $existingPpdm = & $venvPython -c "import keyring; v = keyring.get_password('sure-backup-agent/ppdm', '$ppdmUsername'); print('OK' if v else 'MISSING')"
    if ($existingPpdm.Trim() -eq "OK") {
        Write-Host "  Senha PPDM ja configurada - pulando" -ForegroundColor Yellow
    } else {
        Write-Host "  Senha PPDM NAO esta configurada." -ForegroundColor Yellow
        Write-Host "  Rode em PROMPT SEPARADO (keyring prompt fica invisivel dentro de .ps1):"
        Write-Host "    .\.venv\Scripts\python.exe -m keyring set sure-backup-agent/ppdm $ppdmUsername" -ForegroundColor Cyan
    }
} else {
    Write-Host "  Senha PPDM nao exigida pelo modo '$modeName' - pulando" -ForegroundColor DarkGray
}

# 5c. Senha TIM (so se preciso)
if ($needsTim) {
    $timUsername = & $venvPython -c "import tomllib; print(tomllib.load(open(r'$root\config.toml','rb'))['timeismoney']['username'])"
    $existingTim = & $venvPython -c "import keyring; v = keyring.get_password('sure-backup-agent/timeismoney', '$timUsername'); print('OK' if v else 'MISSING')"
    if ($existingTim.Trim() -eq "OK") {
        Write-Host "  Senha TIM ja configurada - pulando" -ForegroundColor Yellow
    } else {
        Write-Host "  Senha TIM NAO esta configurada." -ForegroundColor Yellow
        Write-Host "  Rode em PROMPT SEPARADO:"
        Write-Host "    .\.venv\Scripts\python.exe -m keyring set sure-backup-agent/timeismoney $timUsername" -ForegroundColor Cyan
    }
} else {
    Write-Host "  Senha TIM nao exigida pelo modo '$modeName' - pulando" -ForegroundColor DarkGray
}

Write-Host "`n=== Setup completo ===" -ForegroundColor Cyan
Write-Host "Proximos passos:"
Write-Host "  1. Salvar as senhas que ficaram pendentes (comandos em ciano acima)"
if ($modeName -ne "all") {
    $sharedDir = & $venvPython -c "import tomllib; print(tomllib.load(open(r'$root\config.toml','rb')).get('mode',{}).get('shared_artifact_dir',''))"
    Write-Host "  2. Conferir acesso ao shared_artifact_dir: $sharedDir" -ForegroundColor Yellow
    Write-Host "     (deve ser leitavel/escrivivel pelo user que vai rodar a task agendada)"
}
Write-Host "  3. Validar smoke: .\.venv\Scripts\python.exe -m src.main"
Write-Host "  4. Instalar tarefa: .\install_task.ps1"
