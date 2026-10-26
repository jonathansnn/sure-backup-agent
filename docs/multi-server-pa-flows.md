# Guia — Fluxos Power Automate pro deploy multi-servidor

Quando as VMs do Veeam, PPDM e Time Is Money estão em **redes diferentes** e não se enxergam, usamos Power Automate + OneDrive como ponte: cada produtor POSTa seu PNG pro PA que salva no OneDrive, e um agregador lê tudo de volta e monta a mensagem única do Teams.

Este guia cobre o **deploy full-split** (3 VMs: PPDM, TIM e Veeam separados).

> As funções de expressão do PA (`base64ToBinary`, `triggerBody`, `setProperty`, `json`, `string`, `base64`, `coalesce`, `length`, `if`, `equals`, `formatDateTime`) **não** são traduzidas — use exatamente como mostrado, mesmo na interface em português.

## Visão geral (full-split)

```
[VM-PPDM]  mode=ppdm        ─POST─► Fluxo D "Store PPDM Artifact" ─► OneDrive: ppdm_latest_AAAAMMDD.{png,json}
                                                                                    │
[VM-TIM]   mode=timeismoney ─POST─► Fluxo B "Store TIM Artifact"  ─► OneDrive: tim_latest_AAAAMMDD.{png,json}
                                                                              + tim_chart_latest_AAAAMMDD.png
                                                                                    │ lê os 2 (3 arquivos)
[VM-Veeam] mode=veeam       ─POST─► Fluxo C' "Aggregate + Send" ◄───────────────────┘
                                              │  (Veeam vem do payload; PPDM e TIM do OneDrive)
                                              └─► Teams (1 mensagem com 4 imagens)
```

> O TIM manda **2 prints**: o topo do dashboard (`tim_latest_*.png`) e o card "Histórico de atividade" (`tim_chart_latest_*.png`). O 2º é opcional — se a captura dele falhar, o arquivo do dia não é criado e a mensagem sai com 3 imagens em vez de 4, sem aviso.

| Modo (`config.toml`) | VM faz | Webhook que essa VM usa | Função do fluxo |
|---|---|---|---|
| `all` (legado) | V+P+TIM single-server | Fluxo A "Send Daily Full" | Posta no Teams direto |
| `ppdm` | só PPDM (last-known-good) | Fluxo D "Store PPDM Artifact" | Salva PNG no OneDrive, não posta |
| `timeismoney` | só TIM | Fluxo B "Store TIM Artifact" | Salva PNG no OneDrive, não posta |
| `veeam` | só Veeam | Fluxo C' "Aggregate + Send" | Lê PPDM **e** TIM do OneDrive, combina, posta |
| `veeam_ppdm` (legado) | V+P juntos | Fluxo C "Aggregate + Send" | Lê só TIM do OneDrive, combina, posta |

**Convenção do webhook:** todas as VMs usam o mesmo NOME de secret no keyring (`sure-backup-agent/teams_webhook`). O VALOR é diferente em cada uma — aponta pro fluxo PA correto pro modo daquela VM.

**Pasta única no OneDrive:** `/sure-backup-agent-artifacts/`. Os 3 fluxos produtores escrevem lá; o agregador lê de lá.

---

## ⚠️ As 4 pegadinhas do PA (leia antes de começar)

Estas mordem em todo fluxo. Já estão aplicadas nos passos abaixo, mas entenda o porquê:

1. **Montar JSON: use `setProperty`, NÃO `createObject`.** `createObject` é função do Adaptive Cards, não do runtime de fluxos — o PA rejeita com *"createObject is not defined"*. `setProperty` encadeado sobre `json('{}')` é o jeito que funciona.
2. **Ler arquivo do OneDrive: use "Obter conteúdo do arquivo usando caminho"**, não "Obter arquivo" (essa pede um *fileId* interno e dá *"Invalid fileId"*).
3. **Parsear JSON lido do OneDrive: envolva com `json(string(...))`.** O OneDrive devolve binário (octet-stream); o Analisar JSON exige string-JSON. Sem isso: *"content must be of type JSON"*.
4. **`contentBytes` do `hostedContents` quando a imagem vem do OneDrive: envolva com `base64(...)`.** O Graph espera string base64; bytes crus dão *"Cannot convert literal to Edm.Binary"*. (Imagens que vêm do payload já são base64 — essas não precisam de `base64()`.)

---

## Fluxo B — "Store TIM Artifact"

**Função:** receber o PNG do TIM da VM-TIM e salvar no OneDrive. Não posta no Teams.

### Passo 1 — Criar o fluxo
1. https://make.powerautomate.com → **Criar** → **Fluxo de nuvem instantâneo**
2. **Nome:** `Sure Backup Agent — Store TIM Artifact`
3. **Gatilho:** "Quando uma solicitação HTTP for recebida" → **Criar**

### Passo 2 — Schema do gatilho HTTP
Método: `POST`. Schema:
```json
{
  "type": "object",
  "properties": {
    "timeismoney_image_b64":   { "type": "string" },
    "timeismoney_error":       { "type": "string" },
    "timeismoney_chart_b64":   { "type": "string" },
    "timeismoney_chart_error": { "type": "string" },
    "timestamp":               { "type": "string" },
    "vm_hostname":             { "type": "string" }
  },
  "required": ["timestamp", "vm_hostname"]
}
```

> ⚠️ **`timeismoney_chart_b64` é o único campo de imagem que pode chegar vazio.** É o 2º print do TIM (card "Histórico de atividade"). Por decisão de produto, se só ele falhar o agente manda o 1º print assim mesmo e deixa este campo em `""` — o motivo vai em `timeismoney_chart_error`. Por isso ele **não** entra em `required` e o Passo 4b é condicional.

Salva. Anota a **URL HTTP POST** (vai pro keyring da VM-TIM).

### Passo 3 — Compor binário TIM
**+ Nova etapa** → **Compor**. Nomear `Compor Binario TIM`. Entradas (expressão):
```
base64ToBinary(triggerBody()?['timeismoney_image_b64'])
```

### Passo 4 — Salvar PNG no OneDrive
**+ Nova etapa** → **OneDrive for Business** → **Criar arquivo**.
- **Caminho da pasta:** `/sure-backup-agent-artifacts/`
- **Nome do arquivo:** `tim_latest_@{utcNow('yyyyMMdd')}.png`
- **Conteúdo do arquivo:** output de `Compor Binario TIM`

> Nome com data (`utcNow('yyyyMMdd')`) evita o erro de "Criar arquivo já existe" e dá 1 arquivo por dia. O agregador lê o do dia atual.

### Passo 4b — Salvar o 2º print (gráfico), só se veio

**+ Nova etapa** → **Controle** → **Condição**.
- **Esquerda** (campo → *Expressão*): `if(empty(triggerBody()?['timeismoney_chart_b64']), 'nao', 'sim')`
- **Operador:** é igual a · **Valor:** `sim`

> Não use o intuitivo `length(...)` **é maior que** `0`: o designer insere a expressão como `"@{...}"` (interpolação de string), o `0` da caixa vai como número, e o `greater()` falha com *"expects two parameter of matching types"*. Comparando `'sim'`/`'nao'`, os dois lados são string. Mesma armadilha do Fluxo C' (Passo 3b-2) — testado em 2026-08-19.

No ramo **"Se sim"**, duas ações:

1. **Compor** → nomear `Compor Binario TIM Chart`, entradas (expressão):
   ```
   base64ToBinary(triggerBody()?['timeismoney_chart_b64'])
   ```
2. **OneDrive → Criar arquivo**
   - **Caminho:** `/sure-backup-agent-artifacts/`
   - **Nome:** `tim_chart_latest_@{utcNow('yyyyMMdd')}.png`
   - **Conteúdo:** output de `Compor Binario TIM Chart`

O ramo **"Se não"** fica **vazio** — de propósito. Sem o 2º print, nenhum arquivo é criado naquele dia, e o Fluxo C' detecta isso pelo `chart_bytes` do metadado (Passo 5). Criar um arquivo vazio seria pior: o Graph rejeita `contentBytes` vazio e derrubaria a mensagem inteira.

### Passo 5 — Compor objeto de metadados
**+ Nova etapa** → **Compor**. Nomear `Compor Meta TIM`. Entradas (expressão — **`setProperty`, não `createObject`**):
```
setProperty(setProperty(setProperty(setProperty(setProperty(setProperty(json('{}'), 'error', coalesce(triggerBody()?['timeismoney_error'], '')), 'timestamp', triggerBody()?['timestamp']), 'hostname', triggerBody()?['vm_hostname']), 'image_bytes', length(coalesce(triggerBody()?['timeismoney_image_b64'], ''))), 'chart_bytes', length(coalesce(triggerBody()?['timeismoney_chart_b64'], ''))), 'chart_error', coalesce(triggerBody()?['timeismoney_chart_error'], ''))
```
Lendo de dentro pra fora: `json('{}')` cria objeto vazio, cada `setProperty` adiciona um campo. Resultado: `{"error":...,"timestamp":...,"hostname":...,"image_bytes":N,"chart_bytes":N,"chart_error":...}`.

> Se você já tem esse fluxo montado, não precisa reescrever a expressão inteira: cole `setProperty(setProperty(` **antes** dela e `, 'chart_bytes', length(coalesce(triggerBody()?['timeismoney_chart_b64'], ''))), 'chart_error', coalesce(triggerBody()?['timeismoney_chart_error'], ''))` **depois**. É a mesma coisa.
>
> `chart_bytes` é o que o Fluxo C' consulta pra saber se o 2º print existe hoje. `chart_error` é o **único** rastro do motivo da falha no lado do Power Automate — a ausência do print é silenciosa na mensagem do Teams.

### Passo 6 — Salvar metadados JSON
**+ Nova etapa** → **OneDrive for Business** → **Criar arquivo**.
- **Caminho:** `/sure-backup-agent-artifacts/`
- **Nome:** `tim_latest_@{utcNow('yyyyMMdd')}.json`
- **Conteúdo do arquivo:** output de `Compor Meta TIM` (o PA serializa pra JSON automaticamente)

### Passo 7 — Resposta HTTP 202
**+ Nova etapa** → **Solicitação** → **Resposta**. Código `202`, corpo `{ "ok": true, "saved": "tim" }`.

Salva. Pega a URL do trigger.

---

## Fluxo D — "Store PPDM Artifact"

**Espelho exato do Fluxo B**, trocando `timeismoney_` por `ppdm_` e `tim_latest` por `ppdm_latest`. Recebe o PNG do PPDM da VM-PPDM (modo `ppdm`, last-known-good) e salva no OneDrive.

> Atalho: no portal PA, abra o **Fluxo B** → **3 pontinhos → Salvar como** → nomeie `Sure Backup Agent — Store PPDM Artifact` e edite só os campos abaixo. Mais rápido que recriar do zero.

### Passo 1 — Nome do fluxo
`Sure Backup Agent — Store PPDM Artifact`

### Passo 2 — Schema do gatilho HTTP
```json
{
  "type": "object",
  "properties": {
    "ppdm_image_b64": { "type": "string" },
    "ppdm_error":     { "type": "string" },
    "timestamp":      { "type": "string" },
    "vm_hostname":    { "type": "string" }
  },
  "required": ["timestamp", "vm_hostname"]
}
```
Salva. Anota a **URL HTTP POST** (vai pro keyring da VM-PPDM).

### Passo 3 — Compor binário PPDM
**Compor** `Compor Binario PPDM`. Entradas:
```
base64ToBinary(triggerBody()?['ppdm_image_b64'])
```

### Passo 4 — Salvar PNG no OneDrive
**OneDrive → Criar arquivo**.
- **Caminho:** `/sure-backup-agent-artifacts/`
- **Nome:** `ppdm_latest_@{utcNow('yyyyMMdd')}.png`
- **Conteúdo:** output de `Compor Binario PPDM`

### Passo 5 — Compor objeto de metadados
**Compor** `Compor Meta PPDM`. Entradas:
```
setProperty(setProperty(setProperty(setProperty(json('{}'), 'error', coalesce(triggerBody()?['ppdm_error'], '')), 'timestamp', triggerBody()?['timestamp']), 'hostname', triggerBody()?['vm_hostname']), 'image_bytes', length(coalesce(triggerBody()?['ppdm_image_b64'], '')))
```

### Passo 6 — Salvar metadados JSON
**OneDrive → Criar arquivo**.
- **Caminho:** `/sure-backup-agent-artifacts/`
- **Nome:** `ppdm_latest_@{utcNow('yyyyMMdd')}.json`
- **Conteúdo:** output de `Compor Meta PPDM`

### Passo 7 — Resposta HTTP 202
Código `202`, corpo `{ "ok": true, "saved": "ppdm" }`.

Salva. Pega a URL do trigger.

> **Nota last-known-good:** no modo `ppdm`, o Python só POSTa pro Fluxo D quando a captura **dá certo** (após até 3 tentativas internas). Se falhar tudo, não POSTa — então o Fluxo D nunca recebe lixo, e o `ppdm_latest` do dia continua sendo o último print bom. Não precisa de lógica especial no fluxo pra isso.

---

## Fluxo C' — "Aggregate + Send" (full-split)

**Função:** receber só o Veeam da VM-Veeam, ler PPDM **e** TIM do OneDrive (gravados pelos Fluxos D e B), montar a mensagem com as 3 imagens e postar no Teams.

> Atalho: clone o Fluxo C existente (que já lê TIM) e adicione a leitura do PPDM. Se for criar do zero, clone o Fluxo A "Send Daily Full".

### Passo 1 — Nome e schema do gatilho
Nome: `Sure Backup Agent — Aggregate + Send (full-split)`. Schema (só Veeam vem no payload):
```json
{
  "type": "object",
  "properties": {
    "veeam_image_b64": { "type": "string" },
    "veeam_error":     { "type": "string" },
    "timestamp":       { "type": "string" },
    "vm_hostname":     { "type": "string" }
  },
  "required": ["timestamp", "vm_hostname"]
}
```
Salva. Anota a **URL HTTP POST** (vai pro keyring da VM-Veeam).

### Passo 2 — Ler PPDM do OneDrive (2 ações)

**2a. Obter PNG PPDM** → **OneDrive → "Obter conteúdo do arquivo usando caminho"**
- **Caminho:** `/sure-backup-agent-artifacts/ppdm_latest_@{utcNow('yyyyMMdd')}.png`

**2b. Obter meta PPDM** → **OneDrive → "Obter conteúdo do arquivo usando caminho"**
- **Caminho:** `/sure-backup-agent-artifacts/ppdm_latest_@{utcNow('yyyyMMdd')}.json`

**2c. Analisar JSON PPDM** → **Operações de Dados → Analisar JSON**
- **Conteúdo** (expressão — **`json(string(...))`**): `json(string(body('Obter_meta_PPDM')))`
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

### Passo 3 — Ler TIM do OneDrive (2 ações)

**3a. Obter PNG TIM** → **"Obter conteúdo do arquivo usando caminho"**
- **Caminho:** `/sure-backup-agent-artifacts/tim_latest_@{utcNow('yyyyMMdd')}.png`

**3b. Obter meta TIM** → **"Obter conteúdo do arquivo usando caminho"**
- **Caminho:** `/sure-backup-agent-artifacts/tim_latest_@{utcNow('yyyyMMdd')}.json`

**3c. Analisar JSON TIM** → **Analisar JSON**
- **Conteúdo:** `json(string(body('Obter_meta_TIM')))`
- **Esquema:** igual ao 2c, **mais dois campos** (só o TIM tem 2º print):
```json
{
  "type": "object",
  "properties": {
    "error":       { "type": "string" },
    "timestamp":   { "type": "string" },
    "hostname":    { "type": "string" },
    "image_bytes": { "type": "integer" },
    "chart_bytes": { "type": "integer" },
    "chart_error": { "type": "string" }
  }
}
```

### Passo 3b — Ler o 2º print do TIM (só se existir hoje)

O arquivo `tim_chart_latest_*.png` **pode não existir** — o Fluxo B só o cria quando o agente conseguiu capturar o gráfico. Ler direto derrubaria o fluxo com 404 nos dias ruins, então a leitura fica dentro de uma condição e o resultado vai pra uma variável.

**3b-1. Inicializar variável** (tem que ser a **primeira** ação do fluxo — o PA exige isso)
- **Nome:** `chart_b64` · **Tipo:** Cadeia de caracteres · **Valor:** *(vazio)*

**3b-2. Condição** (depois do Passo 3c)
- **Esquerda** (campo → *Expressão*): `if(greater(int(coalesce(body('Analisar_JSON_TIM')?['chart_bytes'], 0)), 0), 'sim', 'nao')`
- **Operador:** é igual a · **Valor:** `sim`

> **Por que a comparação inteira fica dentro da expressão** em vez do intuitivo `chart_bytes` **é maior que** `0`: o designer insere expressões como `"@{...}"` (com chaves), que é *interpolação de string* — o resultado chega como texto. O `0` digitado na caixa da direita, por sua vez, vai como número. Aí o `greater()` falha com *"expects two parameter of matching types ... 'String' and 'Integer'"*. Testado e confirmado em 2026-08-19. Comparando `'sim'`/`'nao'`, os dois lados são string e a tipagem do designer deixa de importar.
>
> O `coalesce` também não é preciosismo: no dia em que você atualiza os fluxos, o agregador pode ler um `.json` gravado pelo Fluxo B **antigo**, sem `chart_bytes`. Com o `coalesce`, ausência vira 0 e cai no "Se não" — o comportamento certo.
>
> ⚠️ Modo de falhar silencioso: se você colar essa expressão como **texto comum** em vez de Expressão, a condição nunca dá true, cai sempre no "Se não" e o gráfico some **sem erro nenhum**. Confirme no histórico do run que o ramo "Se sim" executou.

No ramo **"Se sim"**:
1. **OneDrive → "Obter conteúdo do arquivo usando caminho"**, nomear `Obter PNG TIM Chart`
   - **Caminho:** `/sure-backup-agent-artifacts/tim_chart_latest_@{utcNow('yyyyMMdd')}.png`
2. **Definir variável** → `chart_b64` = expressão `base64(body('Obter_PNG_TIM_Chart'))`

Ramo **"Se não"**: vazio. A variável fica `""` e os passos seguintes se viram com isso.

> Por que variável em vez de usar `body('Obter_PNG_TIM_Chart')` direto lá embaixo: o `if()` do Power Automate avalia **os dois** ramos antes de escolher. Referenciar o corpo de uma ação que não executou quebraria a expressão inteira mesmo com a condição "certa". A variável sempre existe.

### Passo 4 — Conteúdo Hospedado (3 imagens + o gráfico)

Na ação **"Postar mensagem"** → **Mostrar opções avançadas** → **Conteúdo Hospedado**. Note a diferença: Veeam vem do **payload** (já base64), PPDM e TIM vêm do **OneDrive** (binário, precisa `base64(...)`), e o gráfico já vem em base64 na variável:

```json
[
  {
    "@microsoft.graph.temporaryId": "1",
    "contentBytes": "@{triggerBody()?['veeam_image_b64']}",
    "contentType": "image/png"
  },
  {
    "@microsoft.graph.temporaryId": "2",
    "contentBytes": "@{base64(body('Obter_PNG_PPDM'))}",
    "contentType": "image/png"
  },
  {
    "@microsoft.graph.temporaryId": "3",
    "contentBytes": "@{base64(body('Obter_PNG_TIM'))}",
    "contentType": "image/png"
  },
  {
    "@microsoft.graph.temporaryId": "4",
    "contentBytes": "@{if(empty(variables('chart_b64')), 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', variables('chart_b64'))}",
    "contentType": "image/png"
  }
]
```

> Aquela string enorme no id 4 é um PNG **1×1 transparente** válido (70 bytes). O Graph rejeita `contentBytes` vazio — derrubaria a mensagem inteira —, então nos dias sem gráfico o slot leva esse pixel invisível e o HTML do Passo 5 simplesmente não o referencia. Ninguém vê nada.

> Os nomes `Obter_PNG_PPDM` / `Obter_PNG_TIM` têm que bater **exatamente** com os nomes que você deu às ações 2a/3a (espaços viram `_`).

### Passo 5 — Mensagem HTML (clica no botão `</>` antes de colar)

```html
<b>Veeam:</b> @{if(equals(triggerBody()?['veeam_error'], ''), 'capturado', concat('AVISO: ', triggerBody()?['veeam_error']))}<br>
<img src="../hostedContents/1/$value" alt="Veeam" width="600"><br><br>
<b>PPDM:</b> @{if(equals(body('Analisar_JSON_PPDM')?['error'], ''), 'capturado', concat('AVISO: ', body('Analisar_JSON_PPDM')?['error']))}<br>
<img src="../hostedContents/2/$value" alt="PPDM Protection Jobs" width="600"><br><br>
<b>Time Is Money:</b> @{if(equals(body('Analisar_JSON_TIM')?['error'], ''), 'capturado', concat('AVISO: ', body('Analisar_JSON_TIM')?['error']))}<br>
<img src="../hostedContents/3/$value" alt="Time Is Money Dashboard" width="600"><br><br>
@{if(empty(variables('chart_b64')), '', '<img src="../hostedContents/4/$value" alt="Time Is Money — Historico de atividade" width="600">')}
```

> A última linha é o 2º print do TIM. Quando o gráfico não foi capturado, o `if()` devolve string vazia e **nada** aparece na mensagem — nem imagem, nem aviso. É a política escolhida (falha silenciosa); o motivo fica em `chart_error` no `tim_latest_*.json` do OneDrive e no `agent.log` da VM-TIM.
>
> Se um dia você quiser tornar isso visível, troque o `''` por `concat('<i>Grafico indisponivel: ', body('Analisar_JSON_TIM')?['chart_error'], '</i>')`.

### Passo 6 — (Opcional) Avisar quando PPDM ou TIM estão atrasados

Pra cada produtor, dá pra comparar a data do meta com hoje e trocar a mensagem de erro. Exemplo pro PPDM, num **Compor** antes de postar:
```
@{if(equals(formatDateTime(body('Analisar_JSON_PPDM')?['timestamp'], 'yyyy-MM-dd'), formatDateTime(utcNow(), 'yyyy-MM-dd')), body('Analisar_JSON_PPDM')?['error'], concat('PPDM artefato eh do dia ', formatDateTime(body('Analisar_JSON_PPDM')?['timestamp'], 'dd/MM'), ' — VM-PPDM nao rodou hoje?'))}
```
Usa o output desse Compor no lugar de `body('Analisar_JSON_PPDM')?['error']` no HTML. Idem pro TIM.

Salva. Pega a URL do trigger.

---

## Configurar as 3 VMs

Em cada VM: edita `[mode] name` no `config.toml`, salva os secrets no keyring e roda `install_task.ps1`.

### VM-PPDM (`mode = "ppdm"`)
```powershell
# config.toml: [mode] name = "ppdm"
python -m keyring set "sure-backup-agent/teams_webhook" "url"   # URL do Fluxo D
python -m keyring set "sure-backup-agent/ppdm" "readonly"        # senha PPDM
```
`install_task.ps1` agenda essa VM com repetição **02:00 → 07:30, a cada 30min** (last-known-good).

### VM-TIM (`mode = "timeismoney"`)
```powershell
# config.toml: [mode] name = "timeismoney"
python -m keyring set "sure-backup-agent/teams_webhook" "url"        # URL do Fluxo B
python -m keyring set "sure-backup-agent/timeismoney" "rootadmin@scale.com"  # senha TIM
```
Agenda 07:55.

### VM-Veeam (`mode = "veeam"`)
```powershell
# config.toml: [mode] name = "veeam"
python -m keyring set "sure-backup-agent/teams_webhook" "url"   # URL do Fluxo C' (NÃO o A!)
```
Agenda 08:00 (5min depois do TIM; PPDM já garantido pela madrugada).

---

## Validação end-to-end

1. **Fluxo D isolado:** smoke na VM-PPDM → run 2xx no portal → `ppdm_latest_AAAAMMDD.png` aparece no OneDrive com o print real.
2. **Fluxo B isolado:** smoke na VM-TIM → `tim_latest_AAAAMMDD.png` **e** `tim_chart_latest_AAAAMMDD.png` aparecem no OneDrive.
3. **Fluxo C' isolado:** smoke na VM-Veeam → mensagem chega no Teams com **as 4 imagens** (Veeam do payload; PPDM, TIM e gráfico do TIM do OneDrive).
3b. **2º print ausente:** na VM-TIM, troca `chart_anchor_text` no `config.toml` por um texto inexistente e roda smoke → `tim_chart_latest` **não** é criado, o log traz `2o print AUSENTE`, e a mensagem do Teams chega com 3 imagens sem quebrar. Desfaz depois.
4. **Last-known-good:** aponta o PPDM url pra um IP morto no `config.toml` da VM-PPDM, roda smoke → log mostra 3 tentativas + "NAO enviando — preservando ultimo artefato bom". O `ppdm_latest` no OneDrive **não muda**.
5. **Ciclo completo:** deixa as 3 tasks agendadas rodarem (PPDM madrugada, TIM 07:55, Veeam 08:00). Confirma a mensagem da manhã.

---

## Troubleshooting

| Sintoma | Causa / fix |
|---|---|
| `createObject is not defined` no Compor Meta | Trocar `createObject` por `setProperty` encadeado (Passo 5 dos fluxos B/D). |
| `Invalid fileId` no Obter conteúdo | Usou "Obter arquivo" (pede fileId). Trocar por **"Obter conteúdo do arquivo usando caminho"**. |
| `content must be of type JSON` no Analisar JSON | Faltou `json(string(...))` no campo Conteúdo. |
| `Cannot convert literal to Edm.Binary` no Postar | `contentBytes` de imagem vinda do OneDrive precisa de `base64(...)`. Veeam (payload) não precisa. |
| Run do Criar arquivo falha "já existe" | Use nome com data `_@{utcNow('yyyyMMdd')}`. |
| Card PPDM/TIM vazio no Teams | `hostedContents` id 2/3 apontando errado, ou a ação "Obter PNG" daquele dia não achou arquivo (produtor não rodou). Confere o arquivo no OneDrive. |
| Gráfico do TIM (4ª imagem) não aparece | **Esperado** quando a captura do card falha — é falha silenciosa por design. Confere `chart_error` no `tim_latest_AAAAMMDD.json` do OneDrive, ou `2o print AUSENTE` no `agent.log` da VM-TIM. Causa mais comum: o título do card mudou → ajusta `[timeismoney].chart_anchor_text` no `config.toml`. Roda `python -m scripts.tim_explore` na VM-TIM pra ver o card ao vivo. |
| Pixel/faixa fina no lugar do gráfico | O HTML está referenciando `hostedContents/4` sem o `if(empty(variables('chart_b64')), ...)` — está mostrando o PNG 1×1 placeholder. Revê o Passo 5. |
| 502 "NoResponse" no POST do agente | Olha o Run History do fluxo no portal: alguma ação interna falhou (conexão OneDrive/Teams expirada, ou arquivo do dia ausente). |
| `File not found` no "Obter PNG ..." **em teste após as 21h BRT** | Não é bug: os caminhos usam `utcNow('yyyyMMdd')` e **já virou o dia em UTC** (21h BRT = 00h UTC). O produtor que rodou de tarde gravou `..._AAAAMM19.png`; o agregador procura `..._AAAAMM20.png`. Na rotina normal (produtores 07:55, agregador 08:00 = 10:55/11:00 UTC) isso nunca acontece. Pra testar à noite: duplique o artefato do dia anterior com o nome de hoje-UTC, ou rode o produtor de novo antes do agregador. |
| PPDM/TIM com data de ontem na mensagem | Produtor não rodou hoje. Pro PPDM: as repetições da madrugada devem cobrir; confere se a task `PPDM Producer` está ativa. |

---

## Apêndice — deploy legado (2 VMs, `veeam_ppdm`)

Se em algum momento você quiser V+P juntos numa VM e só TIM separado (em vez do full-split de 3 VMs), o modo `veeam_ppdm` faz isso: captura Veeam+PPDM localmente e lê só o TIM do OneDrive. Nesse caso use o **Fluxo C** (lê só TIM) em vez do C', e a VM não precisa do Fluxo D. O schema do gatilho do Fluxo C inclui `veeam_*` **e** `ppdm_*` no payload.
