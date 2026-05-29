# Deploy do sure-backup-agent na VM que tem o Veeam

Procedimento pra subir o projeto na máquina que tem o Veeam Console instalado (e também acesso ao PPDM).

## Pré-requisitos na VM-alvo

- Windows 10/11 ou Windows Server
- **Python 3.11 ou superior** instalado e no PATH (testar: abrir PowerShell e rodar `python --version`)
- Acesso à internet (pra baixar dependências e o Chromium do Playwright)
- Veeam Backup & Replication Console instalado e funcional (login com credenciais Windows do usuário corrente)
- Acesso ao PPDM (URL configurada em `config.toml`, usuário `readonly`)
- Senha do usuário PPDM `readonly` em mãos
- URL do trigger HTTP do Power Automate em mãos (mesma que está no Credential Manager da máquina atual — copiar de lá)

## Passo a passo

### 1. Copiar o projeto pra VM-alvo

Use qualquer método (compartilhamento de rede, USB, repositório git, OneDrive). Copia a pasta `c:\sure-backup-agent` inteira, **exceto**:

- `.venv\` (cria de novo na VM-alvo)
- `logs\` (vai ser criada de novo)

Caminho recomendado na VM-alvo: `C:\sure-backup-agent`

### 2. Rodar o setup

Abra PowerShell **como administrador** (recomendado, mas não obrigatório), navegue até a pasta e rode:

```powershell
cd C:\sure-backup-agent
.\setup.ps1
```

O script vai:

1. Verificar que Python 3.11+ está instalado
2. Criar `.venv\` e instalar todas as dependências (incluindo `pywinauto`, `playwright`, etc)
3. Baixar o Chromium do Playwright (~111 MB)
4. Pedir a **URL do trigger do Power Automate** (cola e Enter)
5. Pedir a **senha do PPDM `readonly`** via prompt seguro (digita e Enter — não aparecem caracteres)

Se der erro de execution policy do PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
```

### 3. Validar PPDM

```powershell
.\.venv\Scripts\python.exe -m scripts.ppdm_headless_to_teams
```

Espera-se ver `[OK] Mensagem enviada` no console e a mensagem chegar no canal de teste do Teams (com placeholder vermelho de Veeam e print real do PPDM).

### 4. Explorar Veeam (coleta de logs)

```powershell
.\.venv\Scripts\python.exe -m scripts.veeam_explore
```

O script vai:

- Lançar o Veeam Console (se não estiver aberto)
- Aguardar a janela ficar pronta (pode demorar até 2 min em cold start)
- Dumpar a estrutura completa de controles em `logs\veeam_debug\controls_dump.txt`
- Tirar screenshot da janela em `logs\veeam_debug\veeam_window.png`

**Não interaja com o Veeam enquanto o script roda.**

Quando terminar, copia os 2 arquivos gerados em `logs\veeam_debug\`:
- `controls_dump.txt`
- `veeam_window.png`

E também: tira **um print MANUAL** da tela que você costuma capturar (Home > Jobs) — assim eu sei qual é o alvo da automação.

Envia os 3 arquivos pra continuarmos.

### 5. Configurar o agendamento (depois do veeam_capture pronto)

Esse passo só faz sentido depois que o módulo Veeam estiver funcionando. Será documentado em separado.

## Troubleshooting

| Sintoma | Causa provável | Fix |
|---|---|---|
| `python: command not found` | Python não no PATH | Reinstalar Python marcando "Add to PATH" no instalador |
| `setup.ps1 não pode ser carregado porque a execução de scripts foi desabilitada` | ExecutionPolicy do Windows | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `playwright install` falha com erro de SSL/proxy | Firewall corporativo bloqueando download | Configurar variáveis `HTTPS_PROXY` antes ou usar `--with-deps` |
| `keyring.errors.NoKeyringError` | Backend do keyring não inicializou | Windows Credential Manager deve estar habilitado (geralmente padrão) |
| Veeam não abre / janela não aparece em 120s | Cold start muito lento, ou caminho do executável errado | Aumentar `launch_timeout_seconds` em config.toml; verificar caminho exato |
