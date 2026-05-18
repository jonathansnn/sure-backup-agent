# sure-backup-agent

Automação Python que captura screenshots diários dos consoles de backup (Veeam B&R, Dell PowerProtect Data Manager) e do dashboard administrativo do Time Is Money, e posta os 3 prints num canal do Microsoft Teams via Power Automate — todo dia útil às 08:00 BRT.

Substitui a rotina manual diária do time de infraestrutura sem alterar o formato visual que a diretoria já consome.

## Sumário

- [Motivação](#motivação)
- [Arquitetura](#arquitetura)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Pré-requisitos](#pré-requisitos)
- [Setup rápido](#setup-rápido)
- [Configuração](#configuração)
  - [`config.toml`](#configtoml)
  - [Secrets no Windows Credential Manager](#secrets-no-windows-credential-manager)
- [Power Automate — fluxo Teams](#power-automate--fluxo-teams)
- [Como rodar](#como-rodar)
  - [Smoke test manual](#smoke-test-manual)
  - [Agendamento diário](#agendamento-diário)
- [Como funciona cada captura](#como-funciona-cada-captura)
- [Tratamento de falhas](#tratamento-de-falhas)
- [Logs e troubleshooting](#logs-e-troubleshooting)
- [Testes](#testes)
- [Decisões técnicas e bugs sutis](#decisões-técnicas-e-bugs-sutis)
- [Roadmap](#roadmap)

---

## Motivação

Todo dia útil de manhã, o responsável pela infra precisava:

1. Logar numa VM Windows com acesso aos consoles de backup
2. Tirar print da tela de jobs no **Veeam Backup & Replication Console** (app desktop)
3. Logar no **PPDM** (web), navegar até Jobs > Protection Jobs com filtro 24h, tirar print
4. Logar no **Time Is Money** (web SaaS), abrir o admin-dashboard, tirar print
5. Postar os 3 prints num canal do Teams pra diretoria acompanhar

Quando a pessoa ficava indisponível (férias, doença), a diretoria estranhava a ausência. O `sure-backup-agent` automatiza essa rotina exata — sem mudar o formato — pra que a entrega aconteça sem depender de presença humana.

## Arquitetura

```
+------------------------+
| Task Scheduler (Win)   |
| diario 08:00 BRT       |
+-----------+------------+
            |
            v
   +--------+---------+
   |  src/main.py     |  orquestrador best-effort
   +--+---------+-----+
      |         |
      |  +------+-----------------------+
      |  | src/veeam_capture.py         |  pywinauto + mss (GUI)
      |  | src/ppdm_capture.py          |  Playwright headless (web)
      |  | src/timeismoney_capture.py   |  Playwright headless (web)
      |  +------+-----------------------+
      |         |
      v         v
   +--+---------+-----+
   | src/teams_sender |  POST JSON multipart base64
   +--------+---------+
            |
            v
   +--------+---------+      +-------------------+
   |  Power Automate  | ---> | Canal do Teams    |
   |  HTTP trigger    |      | (diretoria)       |
   +------------------+      +-------------------+
```

**Princípios:**

- **Best-effort por captura** — cada módulo encapsula suas próprias exceções e devolve `(bytes|None, erro|None)`. Falha de um não afeta o outro.
- **Sempre envia pro Teams** — mesmo se as 3 capturas falharem, o post sai com PNGs vermelhos de erro (PIL) e mensagens curtas. A diretoria sempre vê *algo*, indicando que o agente rodou.
- **Fallback em disco** — se o Power Automate estiver indisponível depois de 3 tentativas com backoff exponencial, o payload (sem as imagens base64, que ocupam muito espaço) é salvo em `logs/FAILED_<timestamp>.json` para investigação posterior.
- **Secrets fora do código** — senhas e webhook URL ficam no Windows Credential Manager via `keyring`. O repo não contém nenhum secret.

## Estrutura do projeto

```
sure-backup-agent/
├── src/
│   ├── main.py                  # orquestrador (entry point)
│   ├── config.py                # carrega TOML + secrets do keyring
│   ├── logger.py                # logging rotativo (TimedRotatingFileHandler)
│   ├── veeam_capture.py         # GUI capture via pywinauto + mss
│   ├── ppdm_capture.py          # web capture via Playwright (Keycloak login)
│   ├── timeismoney_capture.py   # web capture via Playwright (Angular SPA)
│   ├── teams_sender.py          # POST + retry + fallback em disco
│   └── image_utils.py           # render_error_png + auto_trim_bottom
├── tests/
│   ├── test_config.py           # 5 testes
│   ├── test_teams_sender.py     # 27 testes
│   ├── conftest.py
│   └── smoke.py                 # smoke E2E só do teams_sender
├── scripts/                     # ferramentas de exploração/calibração (dev)
│   ├── veeam_explore.py         # inspeção visível do Veeam Console
│   ├── ppdm_explore.py          # idem para PPDM (não-headless, slow-mo)
│   ├── ppdm_headless_to_teams.py
│   └── full_e2e_to_teams.py
├── docs/
│   ├── power-automate-setup.md  # como criar o fluxo no PA
│   └── deploy-na-vm-do-veeam.md # roteiro de deploy
├── logs/                        # gitignored: agent.log + FAILED_*.json
├── config.toml                  # config não-secret (versionada)
├── requirements.txt
├── run_daily.bat                # wrapper chamado pelo Task Scheduler
├── install_task.ps1             # cria a tarefa no Task Scheduler
├── setup.ps1                    # bootstrap do venv + dependências
└── README.md
```

## Pré-requisitos

Na **VM-alvo** (a que tem o Veeam Console instalado, geralmente um Windows Server):

- **Windows 10/11 ou Windows Server** com sessão interativa **permanentemente logada** (a tarefa agendada precisa de GUI ativa pro `pywinauto` controlar o Veeam Console — protetor de tela e lock screen quebram isso).
- **Python 3.11+** instalado e disponível no PATH (`python --version` deve responder).
- **Veeam Backup & Replication Console** instalado e configurado (login com credenciais Windows do usuário corrente).
- Acesso de rede ao **PPDM** (default na config: `https://192.168.20.200/`).
- Acesso à internet (pro Time Is Money, pro Chromium e pro POST do Power Automate).
- **Usuário read-only no PPDM** com permissão de ver `Jobs > Protection Jobs`.
- **Usuário admin no Time Is Money** (`rootadmin@scale.com` ou equivalente).
- **Fluxo no Power Automate** criado conforme [docs/power-automate-setup.md](docs/power-automate-setup.md), com a URL HTTP do trigger em mãos.

## Setup rápido

```powershell
git clone https://github.com/jonathansnn/sure-backup-agent.git C:\sure-backup-agent
cd C:\sure-backup-agent
.\setup.ps1
```

O `setup.ps1` vai:

1. Verificar Python 3.11+
2. Criar `.venv\`
3. Instalar dependências (`requirements.txt`)
4. Baixar Chromium do Playwright (~111 MB)
5. Pedir a URL do webhook do Power Automate e salvar no keyring
6. Lembrar o comando pra salvar a senha do PPDM (não rola no script por limitação do keyring + PowerShell)

Depois você salva os 3 secrets manualmente (ver [Secrets](#secrets-no-windows-credential-manager) abaixo) e está pronto pra rodar.

> Se der erro de execution policy: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` antes de rodar `setup.ps1`.

## Configuração

### `config.toml`

Todo parâmetro **não-secret** (paths, URLs, viewport, timeouts, regexes) vive aqui. O arquivo é versionado.

```toml
[veeam]
console_path = "C:\\Program Files\\Veeam\\Backup and Replication\\Console\\veeam.backup.shell.exe"
window_title_regex = "Veeam Backup & Replication"
launch_timeout_seconds = 120     # cold start do Veeam é lento
crop_top = 165                   # remove título + abas Home/View + ribbon
crop_left = 200                  # remove tree pane esquerda
crop_right = 0
crop_bottom = 60                 # remove status bar inferior

[ppdm]
url = "https://192.168.20.200/"
username = "readonly"
login_timeout_seconds = 30
browser = "chromium"
headless = true
viewport_width = 1600
viewport_height = 900
ignore_https_errors = true       # PPDM em IP interno = cert self-signed

[timeismoney]
url = "https://web.timeismoney.tec.br/login"
dashboard_url = "https://web.timeismoney.tec.br/admin-dashboard"
username = "rootadmin@scale.com"
login_timeout_seconds = 30
browser = "chromium"
headless = true
viewport_width = 1600
viewport_height = 1100           # alta o bastante pro anchor + valores
bottom_anchor_text = "Taxa de colaboradores ativos"
bottom_padding_px = 120

[teams]
http_timeout_seconds = 30
retry_attempts = 3
retry_backoff_seconds = 5

[logging]
log_dir = "logs"
level = "INFO"

[keyring]
service_ppdm = "sure-backup-agent/ppdm"
service_timeismoney = "sure-backup-agent/timeismoney"
service_teams_webhook = "sure-backup-agent/teams_webhook"
```

### Secrets no Windows Credential Manager

Setados **uma vez por máquina, por usuário Windows** (o keyring é per-user/per-machine). Sempre use o mesmo usuário que vai rodar o Task Scheduler.

```powershell
# 1. Senha do usuário read-only do PPDM (username = readonly)
python -m keyring set "sure-backup-agent/ppdm" "readonly"

# 2. Senha do admin Time Is Money (username = rootadmin@scale.com — bate com config.toml)
python -m keyring set "sure-backup-agent/timeismoney" "rootadmin@scale.com"

# 3. URL HTTP do trigger do Power Automate (username = "url")
python -m keyring set "sure-backup-agent/teams_webhook" "url"
```

Cada comando abre um prompt invisível ("Password for ... :"). Cole o valor e Enter — não aparecem caracteres digitados, é normal.

**Validar:**

```powershell
python -m keyring get "sure-backup-agent/ppdm" "readonly"
python -m keyring get "sure-backup-agent/timeismoney" "rootadmin@scale.com"
python -m keyring get "sure-backup-agent/teams_webhook" "url"
```

> ⚠️ **Nunca** cole secrets como argumentos de comando — eles ficariam no histórico do PowerShell. O prompt invisível é seguro.

## Power Automate — fluxo Teams

Criação detalhada do fluxo (HTTP trigger → salvar no OneDrive → postar no Teams com `hostedContents`) em [docs/power-automate-setup.md](docs/power-automate-setup.md).

Resumo do schema esperado pelo trigger HTTP:

```json
{
  "type": "object",
  "properties": {
    "veeam_image_b64":      { "type": "string" },
    "veeam_error":          { "type": "string" },
    "ppdm_image_b64":       { "type": "string" },
    "ppdm_error":           { "type": "string" },
    "timeismoney_image_b64":{ "type": "string" },
    "timeismoney_error":    { "type": "string" },
    "timestamp":            { "type": "string" },
    "vm_hostname":          { "type": "string" }
  },
  "required": ["timestamp", "vm_hostname"]
}
```

Convenções do payload:

- `*_image_b64` **sempre** contém base64 de PNG válido. Em caso de falha da captura, o Python gera um PNG vermelho com a mensagem de erro via [`src/image_utils.py`](src/image_utils.py) (`render_error_png`). Isso satisfaz o requisito do `hostedContents` do Graph (que rejeita `contentBytes` vazio).
- `*_error` é o **sinal canônico de sucesso/falha**: string vazia = OK, não-vazia = falha (mensagem curta).
- `timestamp` é **UTC bare** (`"2026-05-18T08:00:00"`, sem offset). O `formatDateTime(addHours(triggerBody()?['timestamp'], -3), 'dd/MM/yyyy HH:mm')` no PA converte pra BRT.

## Como rodar

### Smoke test manual

Roda o pipeline inteiro uma vez. Útil pra validar deploy ou diagnosticar.

```powershell
cd C:\sure-backup-agent
.\.venv\Scripts\python.exe -m src.main
```

Saída esperada nos logs (`logs/agent.log` ou stdout):

```
[INFO] ================ INICIO ================
[INFO] Capturando Veeam Console...
[INFO] Veeam captura OK: 23496 bytes
[INFO] Capturando PPDM Protection Jobs...
[INFO] PPDM captura OK (71954 bytes)
[INFO] Capturando Time Is Money admin-dashboard...
[INFO] TimeIsMoney captura OK (328499 bytes)
[INFO] Envio OK (HTTP 202, run_id=...)
[INFO] ================ FIM (sucesso) ================
```

**Exit codes:**

| Código | Significado |
|---|---|
| 0 | Envio Teams OK (mesmo que capturas tenham falhado parcialmente) |
| 1 | Envio Teams falhou após todas as tentativas (payload salvo em `logs/FAILED_*.json`) |
| 2 | Config inválida (TOML mal-formado ou secret faltando) |

### Agendamento diário

```powershell
.\install_task.ps1
```

Cria a tarefa **"Sure Backup Agent - Daily Report"** no Task Scheduler:

- Roda diariamente às **08:00** sob o usuário corrente
- Logon type **Interactive** (precisa de sessão GUI ativa pro Veeam)
- Limite de 15 min de execução
- Sem retry automático (a lógica de retry do Teams já cobre falhas transitórias)

**Validar:**

```powershell
Start-ScheduledTask -TaskName "Sure Backup Agent - Daily Report"
# aguarda ~30s
Get-ScheduledTaskInfo -TaskName "Sure Backup Agent - Daily Report" | Format-List LastRunTime, LastTaskResult
```

`LastTaskResult = 0` significa sucesso.

## Como funciona cada captura

### Veeam — [`src/veeam_capture.py`](src/veeam_capture.py)

- Usa **pywinauto** (backend UIA) pra encontrar a janela do Veeam Console pelo regex do título.
- Se a janela não existir, tenta abrir o `veeam.backup.shell.exe` e aguarda aparecer.
- Foca + maximiza a janela.
- **Captura via `mss`** (Win32 BitBlt) usando o retângulo da janela — `pywinauto.capture_as_image()` não funciona em janelas WPF (retorna `None` ou coords zeradas).
- Aplica crop configurável (`crop_top/left/right/bottom`) pra remover ribbon + tree pane + status bar.
- **Auto-trim do whitespace inferior** via [`src/image_utils.py:auto_trim_bottom`](src/image_utils.py) — varre linhas de baixo pra cima e remove regiões sem variação de cor, ignorando os últimos 80px (status bar não tem variação útil mas precisa permanecer visível em alguns layouts).

### PPDM — [`src/ppdm_capture.py`](src/ppdm_capture.py)

- **Playwright + Chromium headless** com `ignore_https_errors=True` (cert self-signed em IP interno).
- Login via Keycloak (PPDM usa Keycloak embedded em `/auth/realms/IAM/...`).
- Dispensa o modal **"What's New"** que reaparece toda sessão (bug conhecido do PPDM 19.x) clicando `#whats-new-close`.
- Navega direto pra `#/mgmt/auth2/jobs/protection` por URL (evita menu lateral).
- Aplica filtro **"Last 24 hours"** clicando no botão "All" ao lado de "Start Time:" e selecionando a opção pelo texto.
- Captura um clip ancorado no heading **"Protection Jobs"** — usa o `bounding_box` desse elemento como ponto-zero do retângulo, garantindo que header azul e menu lateral fiquem fora.

### Time Is Money — [`src/timeismoney_capture.py`](src/timeismoney_capture.py)

- Login web em Angular Reactive Form.
- **Ordem importa:** fill email + password **antes** de clicar no radio "Usuário Admin". Clicar no radio re-binda o form do Angular e zera o password se o fill foi feito antes do binding completar (race condition observado).
- Usa `wait_for_url("**/admin-dashboard**")` em vez de `wait_for_load_state("domcontentloaded")` — a navegação do SPA é interna ao router, não dispara load real. Fazer `page.goto` explícito pra forçar o redirect quebra o auth state e o router manda de volta pra /login.
- Capture ancorado no texto **"Taxa de colaboradores ativos"** (último card visível) — clipa do topo da viewport até `anchor.y + height + padding`.

## Tratamento de falhas

| Cenário | Comportamento |
|---|---|
| Veeam Console fechado | Tenta abrir via `veeam.backup.shell.exe`; se ainda assim falhar, devolve erro amigável |
| PPDM inalcançável | Timeout 30s na navegação → PNG vermelho "PPDM offline" no Teams |
| Senha PPDM errada | Keycloak rejeita → timeout no `wait_for_url` → PNG vermelho "PPDM timeout" no Teams |
| TIM senha errada | SPA não navega pra /admin-dashboard → timeout → PNG vermelho no Teams |
| Power Automate retornou 5xx | Retry 3x com backoff exponencial (5s, 10s, 20s) |
| Power Automate retornou 4xx | **Não retenta** (erro de payload/auth — não resolve sozinho) |
| Internet caiu | 3x timeouts → grava `logs/FAILED_<timestamp>.json` com o payload pra reenvio manual |

## Logs e troubleshooting

- **Log principal:** `logs/agent.log` — rotacionado diariamente, 30 dias de retenção (`TimedRotatingFileHandler`).
- **Fallback em disco:** `logs/FAILED_*.json` — payload completo (sem imagens base64) + status de cada tentativa. Aparece quando o Teams falha definitivamente.

**Smoke test em modo visível** (não-headless) pra debug:

```powershell
# inspeção do PPDM com slow-mo + screenshots por passo
.\.venv\Scripts\python.exe -m scripts.ppdm_explore

# inspeção do Veeam
.\.venv\Scripts\python.exe -m scripts.veeam_explore
```

**Erros comuns:**

| Sintoma | Diagnóstico |
|---|---|
| `ConfigError: Secret não encontrado` | Secret não foi salvo no keyring **dessa máquina** com **esse user**. Setar via `python -m keyring set ...`. |
| `Veeam executavel nao encontrado` | Ajustar `console_path` no `config.toml`. |
| `PPDM timeout` na navegação inicial | Cert self-signed muito quebrado ou OCSP responder inalcançável. Tentar adicionar `--ignore-certificate-errors` no `args` do `browser.launch()`. |
| `PPDM timeout` na espera de URL não-login | Senha errada (Keycloak rejeitou). Resetar via `keyring set`. |
| `TimeIsMoney timeout` em `**admin-dashboard**` | Senha errada. Validar manualmente no browser. |

## Testes

32 testes unitários (config + teams_sender):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

Cobertura:

- **`tests/test_config.py`** — load de TOML, injeção de secrets, ConfigError quando falta secret obrigatório
- **`tests/test_teams_sender.py`** — `build_payload`, política de retry por status HTTP, _post_once com mocks de `requests`, send com retry + fallback em disco

Tests **não** cobrem captures (Veeam/PPDM/TIM) — esses dependem do ambiente real e são validados via smoke E2E manual.

## Decisões técnicas e bugs sutis

Lições aprendidas durante o desenvolvimento, anotadas pra quem for manter:

1. **Veeam é WPF** — `pywinauto.capture_as_image()` retorna `None` ou coords zeradas. Solução: usar `mss` (BitBlt) com o rect da janela vindo do `pywinauto`. Também: `print_control_identifiers()` trava em WPF — não introspectar.

2. **PPDM "What's New" modal reaparece toda sessão** mesmo com "Don't show again" marcado. Bug do PPDM 19.x — temos handler explícito.

3. **PPDM usa Keycloak** — o `wait_for_url(lambda url: "login" not in url.lower())` funciona porque após sucesso, Keycloak redireciona pro callback que volta pro hash route do PPDM (sem "login"). Senha errada deixa o URL em `/login-actions/authenticate` (com "login") → timeout. Sintoma de senha errada == timeout, não erro 401.

4. **Time Is Money Angular Reactive Form** zera password se `fill()` rodar antes do form control completar binding. Workaround: fill **antes** de clicar no radio "Usuário Admin" (que dispara o re-bind). Os 2 radios são parte do mesmo form, só mudam o value submetido.

5. **TIM é uma SPA** — `wait_for_load_state("domcontentloaded")` retorna imediatamente após a click no Entrar (a página de login já tinha disparado load). Precisa `wait_for_url` pra capturar a navegação interna do router. `page.goto()` explícito pra forçar o dashboard quebra o auth state.

6. **PowerShell 5.1 quirks** — arquivos `.ps1` sem BOM são lidos como Windows-1252; o `setup.ps1` é ASCII puro pra evitar isso. `&` (call operator) faz o prompt do `keyring set` ficar invisível — por isso o setup pede pra rodar o keyring num prompt separado.

7. **`hostedContents` do Microsoft Graph** rejeita `contentBytes` vazio. Por isso `build_payload` **sempre** gera um PNG (PNG de erro vermelho via `render_error_png` quando a captura falhou). O sinal canônico de falha é o campo `*_error`, não a ausência de imagem.

8. **`formatDateTime` do PA com offset** — `convertFromUtc` rejeita timestamps com offset suffix e com fractional seconds. Por isso o Python envia UTC bare (`"2026-05-18T08:00:00"`) e o PA faz `addHours(..., -3)` pra BRT.

9. **Keyring é per-user/per-machine** — secrets setados numa VM **não** vão pra outra. Cada VM precisa ter os 3 secrets salvos sob o user que vai rodar a task.

## Roadmap

**Stage 2 (próximo):** intelligence layer.

Em vez de só postar prints, detectar status crítico:

- PPDM: parsing do DOM da tabela de jobs (procurar células com classe de "Failed"). Se houver, enviar mensagem de alerta no Teams ao invés do print.
- Veeam: usar `Veeam.Backup.PowerShell` cmdlets via `Get-VBRBackupSession` em vez de captura GUI. Permite enviar texto rico ("3 jobs OK, 1 falha em SRV-DC") e até clicar pra detalhes.

Mudanças quando der: nada na infra atual, só novos módulos + uma flag de modo no `main.py`.

**Pequenas melhorias acumuladas:**

- Detectar "invalid credentials" no DOM do PPDM/TIM pra erro descritivo em vez de timeout de 30s.
- Capturar HTML/screenshot do estado de falha em `logs/` automaticamente (hoje só com `debug_dir=...`).
- Suporte a múltiplos canais Teams (dev/staging/prod) via flag.

---

**Mantenedor:** [Jonathan](https://github.com/jonathansnn) — infoalter.

**Licença:** uso interno.
