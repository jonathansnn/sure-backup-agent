# Guia — Criar o fluxo do Power Automate

Este documento descreve, passo a passo, como criar o fluxo do Power Automate que vai receber os screenshots do `sure-backup-agent` e postar no canal do Teams da diretoria.

> Os nomes de ações e campos abaixo seguem a interface em **português**. As funções de expressão do Power Automate (`base64ToBinary`, `triggerBody`, `formatDateTime`, `if`, `concat`, `length`, `greater`) **não** são traduzidas — use exatamente como mostrado.

## Visão geral

O fluxo é simples no conceito:

```
[Solicitação HTTP recebida] → [Salvar imagens no OneDrive] → [Postar mensagem no canal do Teams com as imagens anexadas]
```

**Por que salvar no OneDrive primeiro?** O Teams não consegue exibir imagens em base64 inline numa mensagem comum. A solução padrão é: o Power Automate salva a imagem temporariamente no OneDrive, pega o link, e anexa esse link na mensagem do Teams.

---

## Passo 1 — Criar o fluxo

1. Acesse https://make.powerautomate.com com sua conta corporativa (acessos@infoalter.com.br).
2. **Criar** → **Fluxo de nuvem instantâneo**.
3. **Nome do fluxo**: `Sure Backup Agent — Relatório Diário`.
4. **Gatilho**: escolha **"Quando uma solicitação HTTP for recebida"** (categoria *Solicitação*).
5. Clique **Criar**.

## Passo 2 — Configurar o gatilho HTTP

No bloco *Quando uma solicitação HTTP for recebida*:

- **Método**: `POST`
- **Esquema JSON do Corpo da Solicitação**: cole o schema abaixo

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

> **Convenção do payload**: campos `*_image_b64` e `*_error` são sempre strings. String vazia (`""`) significa "ausente". Isso evita dor de cabeça com `null` no Power Automate, que historicamente trata `null` de forma inconsistente.

**Salve o fluxo uma vez** (canto superior direito). Só após salvar, o Power Automate gera a **URL HTTP POST** no próprio bloco do gatilho — copie e guarde essa URL, ela é o "webhook" que o Python vai chamar. Trate como secret (qualquer um com a URL consegue postar no canal).

## Passo 3 — Decodificar o base64 das imagens (ações Compor)

Adicione **duas ações Compor** (uma para cada imagem) que decodificam o base64 em binário:

- **Compor Binário Veeam**
  - Entradas (use a expressão): `base64ToBinary(triggerBody()?['veeam_image_b64'])`
- **Compor Binário PPDM**
  - Entradas (use a expressão): `base64ToBinary(triggerBody()?['ppdm_image_b64'])`

> Por que Compor? Porque o conector OneDrive aceita binário diretamente na propriedade "Conteúdo do Arquivo", e Compor é a forma idiomática de transformar uma expressão num valor reutilizável.

## Passo 4 — Salvar imagens no OneDrive (condicional)

Para cada imagem, só salvar se a string não for vazia. Use uma ação **Condição** envolvendo um **Criar arquivo**.

### Veeam

- **Condição**: `length(triggerBody()?['veeam_image_b64'])` **é maior que** `0`
- **Em caso afirmativo**:
  - **OneDrive para Empresas → Criar arquivo**
    - Caminho da Pasta: `/SureBackupAgent/` (cria a pasta se não existir)
    - Nome do Arquivo: use a expressão `concat('veeam_', formatDateTime(triggerBody()?['timestamp'], 'yyyyMMdd_HHmmss'), '.png')`
    - Conteúdo do Arquivo: saída de **Compor Binário Veeam**
  - A saída do **Criar arquivo** contém o link do arquivo (vai ser usado no Passo 6).

### PPDM

- Igual à Veeam, trocando `veeam` por `ppdm` em todos os lugares (nome do Compor, nome do arquivo, expressão da Condição).

> **Limpeza periódica:** o OneDrive vai acumular PNGs. Sugestão: criar um segundo fluxo agendado semanal que deleta arquivos da pasta `/SureBackupAgent/` com mais de 30 dias. Fora de escopo nesta iteração, mas vale anotar.

## Passo 5 — Compor a mensagem do Teams

Use uma ação **Compor** chamada **Compor Mensagem HTML** para montar o corpo da mensagem.

Entradas (em HTML — o Teams renderiza tags básicas como `<b>`, `<br>`, `<i>`):

```html
<b>📊 Relatório diário de backups — @{triggerBody()?['vm_hostname']}</b><br>
<i>@{formatDateTime(triggerBody()?['timestamp'], 'dd/MM/yyyy HH:mm')}</i><br><br>

<b>Veeam:</b> @{if(greater(length(triggerBody()?['veeam_image_b64']), 0), '✅ capturado', concat('⚠️ ', triggerBody()?['veeam_error']))}<br>
<b>PPDM:</b> @{if(greater(length(triggerBody()?['ppdm_image_b64']), 0), '✅ capturado', concat('⚠️ ', triggerBody()?['ppdm_error']))}<br>
```

> Imagens embutidas em mensagens nativas do Teams via Power Automate funcionam melhor através de adaptive cards. Mas como ponto de partida, anexar os arquivos do OneDrive na própria mensagem do canal já cumpre o objetivo (diretoria clica e vê a imagem em tela cheia, igual a um anexo de email).

## Passo 6 — Postar no Teams

Use a ação **Microsoft Teams → Postar mensagem em um chat ou canal**.

- **Postar como**: `Bot do Flow` (ou `Bot de Fluxo`, depende da versão)
- **Postar em**: `Canal`
- **Equipe**: selecione a equipe da diretoria (ou a sua equipe de teste)
- **Canal**: o canal alvo (comece pelo **canal de teste**)
- **Mensagem**: saída de *Compor Mensagem HTML*
- **Assunto** (opcional): use a expressão `concat('Relatório de Backup - ', formatDateTime(triggerBody()?['timestamp'], 'dd/MM/yyyy'))`
- **Anexos do arquivo** (se disponível na sua licença): anexe os dois arquivos do OneDrive (saídas dos *Criar arquivo*)

## Passo 7 — Testar o fluxo

Antes de chamar do Python, dispare manualmente:

1. No editor do fluxo, clique **Testar** → **Manualmente** → **Salvar e Testar** → **Executar**.
2. Use **Postman** ou **curl** para postar na URL do gatilho (copiada no Passo 2):

```bash
curl -X POST "<URL_DO_GATILHO>" ^
  -H "Content-Type: application/json" ^
  -d "{\"veeam_image_b64\":\"\",\"veeam_error\":\"teste — captura desativada\",\"ppdm_image_b64\":\"\",\"ppdm_error\":\"teste — captura desativada\",\"timestamp\":\"2026-05-16T08:00:00-03:00\",\"vm_hostname\":\"TEST-VM\"}"
```

> O `^` no final das linhas é para quebrar comando em múltiplas linhas no cmd do Windows. Se rodar tudo numa linha só, remove os `^`.

3. Vá ao canal de teste do Teams — a mensagem deve aparecer com os dois ⚠️ e os textos de erro do teste.

4. Quando estiver tudo OK no canal de teste, repita com uma imagem real em base64 (pode usar https://www.base64encode.org/ para converter um PNG pequeno) para validar que o anexo aparece corretamente.

## Quando concluído

Me devolve aqui:

1. **URL do gatilho HTTP** (depois de salvar o fluxo) — vou guardar no Windows Credential Manager via `keyring`.
2. Confirmação de que a mensagem de teste apareceu no canal de teste.
3. Qualquer ajuste de schema/campos que você precisou fazer (caso o Power Automate tenha reclamado de algo).

A partir daí, começamos a codar.
