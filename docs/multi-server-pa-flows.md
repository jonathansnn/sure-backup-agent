# Guia — Fluxos Power Automate pro deploy multi-servidor

Este documento descreve a criação dos **2 novos fluxos PA** necessários quando as VMs do Veeam e do Time Is Money não estão na mesma rede e precisam usar Power Automate + OneDrive como ponte.

## Visão geral

```
[VM-TIM]   07:55   ─POST─►  Fluxo B "Store TIM Artifact"   ─► OneDrive: tim_latest.{png,json}
                                                                              │
                                                                              │ lê
[VM-V+P]   08:00   ─POST─►  Fluxo C "Aggregate + Send"  ────────────────────┘
                                                                              │
                                                                              └─► Teams (1 mensagem)
```

| Modo | Webhook que essa VM usa | Fluxo |
|---|---|---|
| `all` (legado, single-server) | URL do fluxo "Send Daily Full" (atual) | A (já existe) |
| `timeismoney` | URL do fluxo "Store TIM Artifact" | B (novo) |
| `veeam_ppdm` | URL do fluxo "Aggregate + Send" | C (novo) |

**Convenção importante:** todas as VMs usam o mesmo NOME de secret no keyring (`sure-backup-agent/teams_webhook`). O VALOR é diferente em cada uma — aponta pro fluxo PA correto pro modo da VM.

---

## Fluxo B — "Store TIM Artifact"

**Função:** receber o PNG do TIM da VM produtora e salvar no OneDrive. Não posta nada no Teams.

### Passo 1 — Criar o fluxo

1. Em https://make.powerautomate.com → **Criar** → **Fluxo de nuvem instantâneo**
2. **Nome:** `Sure Backup Agent — Store TIM Artifact`
3. **Gatilho:** "Quando uma solicitação HTTP for recebida"
4. **Criar**

### Passo 2 — Schema do gatilho HTTP

Método: `POST`. Schema:

```json
{
  "type": "object",
  "properties": {
    "timeismoney_image_b64": { "type": "string" },
    "timeismoney_error":     { "type": "string" },
    "timestamp":             { "type": "string" },
    "vm_hostname":           { "type": "string" }
  },
  "required": ["timestamp", "vm_hostname"]
}
```

Salva. Anota a **URL HTTP POST** gerada (é o que vai pro keyring da VM-TIM).

### Passo 3 — Action: Compor binário TIM

**+ Nova etapa** → **Compor** (Compose). Nomear `Compor Binario TIM`. Expressão:

```
base64ToBinary(triggerBody()?['timeismoney_image_b64'])
```

### Passo 4 — Action: Salvar PNG no OneDrive

**+ Nova etapa** → **OneDrive for Business** → **Criar arquivo**.

- **Caminho da pasta:** `/sure-backup-agent-artifacts/` (cria se não existir)
- **Nome do arquivo:** `tim_latest.png` (fixo — sempre sobrescreve)
- **Conteúdo do arquivo:** Output da etapa `Compor Binario TIM`

> ⚠️ "Criar arquivo" do OneDrive falha se o arquivo já existe. Use **"Atualizar arquivo"** se preferir, OU adicione uma ação prévia "Excluir arquivo" com `-ErrorAction Ignore` (na verdade no PA é "Configurar Try-Catch" em torno do excluir).
>
> **Solução mais simples:** use a ação **"Criar arquivo"** com nome único por execução tipo `tim_latest_@{utcNow('yyyyMMdd')}.png`. Aí cada dia tem seu arquivo. O Fluxo C lê o do dia atual.

### Passo 5 — Action: Compor objeto de metadados

**+ Nova etapa** → **Operações de Dados** → **Compor**. Nomear `Compor Meta TIM`.

No campo **Entradas**, cola **só** essa expressão (clica em "Expressão" / `fx`, cola, OK):

```
createObject('error', coalesce(triggerBody()?['timeismoney_error'], ''), 'timestamp', triggerBody()?['timestamp'], 'hostname', triggerBody()?['vm_hostname'], 'image_bytes', length(coalesce(triggerBody()?['timeismoney_image_b64'], '')))
```

> Por que `createObject` em vez de montar JSON via `concat` de strings? Construir JSON por concatenação quebra se a mensagem de erro tiver aspas, quebra de linha ou qualquer caractere especial. `createObject` devolve um objeto nativo do PA e a serialização pra JSON é automática + segura. `coalesce(x, '')` blinda contra `null` quando o campo está ausente no payload.

### Passo 6 — Action: Salvar metadados JSON

**+ Nova etapa** → **OneDrive for Business** → **Criar arquivo**.

- **Caminho:** `/sure-backup-agent-artifacts/`
- **Nome:** `tim_latest.json` (ou `tim_latest_@{utcNow('yyyyMMdd')}.json` se for por dia)
- **Conteúdo do arquivo:** output da etapa `Compor Meta TIM` (o PA serializa o objeto pra JSON automaticamente)

### Passo 7 — Action: Response HTTP 202

**+ Nova etapa** → **Solicitação** → **Resposta**.

- **Código de Status:** `202`
- **Corpo:** `{ "ok": true, "saved": "tim_latest.png" }`

Salva o fluxo. Pega a URL HTTP do trigger.

---

## Fluxo C — "Aggregate + Send"

**Função:** receber V+P da VM agregadora, ler TIM do OneDrive (que o Fluxo B gravou), montar mensagem combinada, postar no Teams.

### Passo 1 — Duplicar o fluxo existente

A forma mais rápida é **clonar o fluxo "Send Daily Full"** (existente) e modificar.

1. No portal PA → seu fluxo atual → **3 pontinhos** → **Salvar como**
2. Nomear: `Sure Backup Agent — Aggregate + Send`
3. Abrir o clone pra editar

### Passo 2 — Modificar schema do gatilho

Remove os campos TIM do schema (eles vêm do OneDrive agora, não do payload):

```json
{
  "type": "object",
  "properties": {
    "veeam_image_b64": { "type": "string" },
    "veeam_error":     { "type": "string" },
    "ppdm_image_b64":  { "type": "string" },
    "ppdm_error":      { "type": "string" },
    "timestamp":       { "type": "string" },
    "vm_hostname":     { "type": "string" }
  },
  "required": ["timestamp", "vm_hostname"]
}
```

Salva. Anota a **URL HTTP POST** (vai pro keyring da VM-V+P).

### Passo 3 — Adicionar leitura do TIM do OneDrive

Logo no início do fluxo (antes de qualquer Compor), adiciona 2 ações:

**3a. Obter conteúdo do arquivo TIM (PNG)**

**+ Nova etapa** → **OneDrive for Business** → **Obter conteúdo do arquivo usando caminho**.

- **Caminho:** `/sure-backup-agent-artifacts/tim_latest.png` (ou com sufixo de data se usou variante)

**3b. Obter metadados TIM (JSON)**

**+ Nova etapa** → **OneDrive for Business** → **Obter conteúdo do arquivo usando caminho**.

- **Caminho:** `/sure-backup-agent-artifacts/tim_latest.json`

**3c. Parsear metadados TIM**

**+ Nova etapa** → **Operações de Dados** → **Analisar JSON**.

- **Conteúdo:** output da etapa 3b
- **Esquema:**

```json
{
  "type": "object",
  "properties": {
    "error":       { "type": "string" },
    "timestamp":   { "type": "string" },
    "hostname":    { "type": "string" },
    "image_bytes": { "type": "integer" }
  }
}
```

### Passo 4 — Substituir as referências TIM no fluxo

Onde o fluxo antigo usava:

| Antes (fluxo legado) | Depois (Fluxo C) |
|---|---|
| `triggerBody()?['timeismoney_image_b64']` (string base64) | conteúdo binário direto da etapa 3a |
| `triggerBody()?['timeismoney_error']` | `body('Analisar_JSON')?['error']` |

**Mais especificamente:**

- A etapa **"Compor Binário TimeIsMoney"** que antes fazia `base64ToBinary(triggerBody()?['timeismoney_image_b64'])` agora vira só uma **Compor** apontando direto pro output da etapa 3a (já vem binário).
- Na ação **"Postar como Usuário"**, no campo **Conteúdo Hospedado**, o terceiro objeto (TIM) muda o `contentBytes` pra apontar pra etapa 3a:

```json
{
  "@microsoft.graph.temporaryId": "3",
  "contentBytes": "@{body('Obter_conteudo_TIM_PNG')}",
  "contentType": "image/png"
}
```

(o nome exato da etapa depende de como você nomeou a 3a)

- Na ação **Compor Mensagem HTML**, a linha do TIM muda pra:

```html
<b>Time Is Money:</b> @{if(equals(body('Analisar_JSON')?['error'], ''), 'capturado', concat('AVISO: ', body('Analisar_JSON')?['error']))}<br>
<img src="../hostedContents/3/$value" alt="Time Is Money Dashboard" width="600">
```

### Passo 5 — (Opcional) Detectar TIM stale

Pra alertar quando o artefato TIM está velho (VM-TIM não rodou hoje):

Antes da ação de postar, adiciona uma **Condição**:

- **Condição:** `formatDateTime(body('Analisar_JSON')?['timestamp'], 'yyyy-MM-dd')` **é igual a** `formatDateTime(utcNow(), 'yyyy-MM-dd')`
- **Sim:** continua normal
- **Não:** sobrescreve o erro TIM via Compor:

```
@{concat('TIM artefato eh do dia ', formatDateTime(body('Analisar_JSON')?['timestamp'], 'dd/MM'), ' — VM-TIM nao rodou hoje?')}
```

E usa essa variável composta no lugar de `body('Analisar_JSON')?['error']` no HTML.

Salva o fluxo. Pega a URL HTTP do trigger.

---

## Configurar as VMs

### VM-TIM (modo `timeismoney`)

`config.toml`:

```toml
[mode]
name = "timeismoney"
```

Keyring:

```powershell
# URL do Fluxo B
python -m keyring set "sure-backup-agent/teams_webhook" "url"
# Senha do TIM
python -m keyring set "sure-backup-agent/timeismoney" "rootadmin@scale.com"
```

### VM-V+P (modo `veeam_ppdm`)

`config.toml`:

```toml
[mode]
name = "veeam_ppdm"
```

Keyring:

```powershell
# URL do Fluxo C (NÃO o A!)
python -m keyring set "sure-backup-agent/teams_webhook" "url"
# Senha do PPDM
python -m keyring set "sure-backup-agent/ppdm" "readonly"
```

---

## Validação end-to-end

1. **Validar Fluxo B isoladamente:** roda smoke na VM-TIM → confirma no portal PA que o run de "Store TIM Artifact" foi 2xx → confirma no OneDrive que os arquivos `tim_latest.png` e `tim_latest.json` apareceram.
2. **Validar Fluxo C isoladamente:** roda smoke na VM-V+P → confirma run 2xx no PA → mensagem combinada chega no Teams com as 3 imagens corretas (V+P do payload, TIM do OneDrive).
3. **Validar timing:** agenda VM-TIM pras 07:55 e VM-V+P pras 08:00. Espera o ciclo diário rodar. Confirma que a mensagem no Teams tem TIM atualizado.
4. **Validar staleness:** desliga a VM-TIM. Roda smoke na VM-V+P. A mensagem no Teams ainda chega (com TIM antigo, ou com aviso "TIM artefato eh do dia X" se você implementou o Passo 5 do Fluxo C).

---

## Troubleshooting

| Sintoma | Diagnóstico |
|---|---|
| Run do Fluxo B falha em "Criar arquivo" | Arquivo já existe e a ação não está em modo overwrite. Usa "Atualizar arquivo" ou adiciona sufixo de data no nome. |
| Run do Fluxo C falha em "Obter conteúdo do arquivo" | VM-TIM nunca rodou ou o caminho do OneDrive está diferente entre os 2 fluxos. Confere `/sure-backup-agent-artifacts/tim_latest.png` no OneDrive manualmente. |
| Mensagem no Teams sem imagem do TIM | `hostedContents` ID `3` tá apontando pra string vazia. Confere se a etapa 3a foi referenciada certo no `contentBytes`. |
| TIM aparece com PNG de "erro desconhecido" mesmo a captura tendo funcionado na VM-TIM | A VM-TIM enviou OK mas o Fluxo C leu uma versão velha do OneDrive. Pode ser latência de sync. Tenta atrasar a VM-V+P em mais 5min. |
