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
Write-Host "  Voce vai precisar fornecer 2 secrets:"
Write-Host "    a) URL HTTP do gatilho do Power Automate (webhook do fluxo Teams)"
Write-Host "    b) Senha do usuario read-only do PPDM"
Write-Host ""

# 5a. Webhook URL
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

# 5b. Senha PPDM (le username do config.toml)
$ppdmUsername = & $venvPython -c "import tomllib; print(tomllib.load(open(r'$root\config.toml','rb'))['ppdm']['username'])"
$existingPpdm = & $venvPython -c "import keyring; v = keyring.get_password('sure-backup-agent/ppdm', '$ppdmUsername'); print('OK' if v else 'MISSING')"
if ($existingPpdm.Trim() -eq "OK") {
    Write-Host "  Senha PPDM ja configurada - pulando" -ForegroundColor Yellow
} else {
    Write-Host "  Senha PPDM NAO esta configurada." -ForegroundColor Yellow
    Write-Host "  Rode o comando abaixo em UM PROMPT SEPARADO (o prompt do keyring fica invisivel"
    Write-Host "  quando invocado dentro de um script .ps1):"
    Write-Host ""
    Write-Host "    .\.venv\Scripts\python.exe -m keyring set sure-backup-agent/ppdm $ppdmUsername" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Vai aparecer: Password for '$ppdmUsername' in 'sure-backup-agent/ppdm':"
    Write-Host "  Digite a senha (nao aparecem caracteres) e Enter."
}

Write-Host "`n=== Setup completo ===" -ForegroundColor Cyan
Write-Host "Proximos passos:"
Write-Host "  1. (Se ainda nao fez) Configurar senha PPDM (comando acima)"
Write-Host "  2. Validar PPDM:                .\.venv\Scripts\python.exe -m scripts.ppdm_headless_to_teams"
Write-Host "  3. Explorar Veeam:              .\.venv\Scripts\python.exe -m scripts.veeam_explore"
