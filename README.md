# mcp-planalto

Servidor [MCP](https://modelcontextprotocol.io) para **pesquisar, ler e mapear a legislação federal brasileira**, sempre na versão mais recente. Ele junta duas fontes oficiais:

| Fonte | O que fornece |
|---|---|
| **Planalto** (`planalto.gov.br/ccivil_03`) | Texto compilado e atualizado de cada norma, com as notas "(Redação dada pela Lei nº …)", e os quadros anuais de leis, decretos, MPs, LCs e ECs. |
| **Senado Federal – Dados Abertos** (`legis.senado.leg.br/dadosabertos`) | Indexação temática (tesauro) de cada norma e o **grafo de alterações**: quais normas alteraram, revogaram ou regulamentaram cada dispositivo. |

Com isso, um assistente (Claude Desktop, Claude Code ou qualquer cliente MCP) consegue:

- ler um artigo exato, na redação vigente ou com o histórico de redações;
- saber quem alterou um dispositivo e quando;
- **conferir se o texto compilado do Planalto já incorpora todas as alterações conhecidas**;
- acompanhar o que foi publicado nos últimos dias, filtrado por tema;
- **mapear todo o acervo de um tema** (ex.: IRPJ) e exportar para planilha.

> O texto do Planalto não substitui o publicado no Diário Oficial da União.

---

## Ferramentas MCP

| Ferramenta | Para quê |
|---|---|
| `ler_norma` | Texto atualizado de uma norma ou de um dispositivo (`"art. 74, § 12"`, `"arts. 15 a 20"`, `"§ 1º do art. 44"`, `"art. 44, caput"`, `"art. 76 do ADCT"`, `"anexo I"`), modo vigente ou histórico, busca por termo dentro da norma (inclusive anexos), paginação. Ao ler um dispositivo, traz o caput a que ele se liga e um **alerta** quando: (a) a redação mostrada só produz efeitos no futuro (o servidor segue a nota "(Produção de efeitos)"/"(Vigência)" até a cláusula da outra lei, calcula a data e mostra a redação que ainda se aplica); (b) há revogação com data marcada (ex.: LC 214/2025, art. 542, sobre a legislação do PIS/Cofins, a partir de 1º/1/2027); (c) o Senado registra alteração recente que o texto ainda não mostra; (d) a redação veio de MP que perdeu a eficácia. Quando a redação lida foi dada por lei dos últimos ~2 anos, mostra também a **ementa e a cláusula de vigência dessa lei** e, pelo artigo da lei nova que fez a alteração (o link da nota aponta para ele), **qual inciso da cláusula vale para o dispositivo lido e a data de efeitos** (ex.: art. 9º, § 2º, da Lei 9.249 → LC 224/2025, art. 8º → art. 14, III → 1º/1/2026), com alerta se a data for futura. |
| `estrutura_norma` | Sumário (livros, títulos, capítulos, seções e os artigos de cada um). Útil em normas grandes (RIR/2018, LC 214/2025, Código Civil). |
| `consultar_norma` | Ficha: ementa, data, apelido, situação (revogada, convertida, sem eficácia), URN LexML, publicação no DOU, indexação do Senado, nº de normas alteradoras, regulamentos. |
| `buscar_normas` | Busca no catálogo por ementa, apelido e indexação, sem acento, caixa ou flexão ("tributária" acha "tributário"). Aceita `"frase exata"`, `OU`, `A/B` (um ou outro), `prefixo*`, filtros de tipo e ano; a frase exata vem primeiro; siglas como IRRF e IRPJ são expandidas. |
| `historico_alteracoes` | Normas que alteraram a norma (ou um dispositivo, inclusive do ADCT), com as ações (alteração, acréscimo, revogação) e os dispositivos atingidos. Ressalvas, regulamentações e conversões vêm à parte; reedições de MP, agrupadas (também as renumeradas). Também o caminho inverso: o que a norma alterou, cruzado com as cláusulas "passa a vigorar"/"ficam revogados" do próprio texto, porque o registro do Senado pode estar incompleto. |
| `verificar_atualizacao` | Para cada norma alteradora registrada pelo Senado, procura, artigo por artigo, a nota "(Redação dada/Incluído/Revogado pela …)" no dispositivo alterado; confere se acréscimos existem (e não são homônimos incluídos por outra lei) e se revogações aparecem. Também procura leis dos últimos ~13 meses cuja ementa diz alterar esta. Lista redações e revogações com efeitos futuros. Separa o que falta no texto (ATENÇÃO), o que está a conferir (só em "Vide", incorporação parcial, dispositivo inexistente) e MPs encerradas. |
| `novidades_legislativas` | Normas publicadas nos últimos N dias (relê os quadros do Planalto na hora), com filtro por tema. |
| `mapear_tema` | Acervo completo de um tema, com relevância e motivo de cada norma; exporta `.xlsx` ou `.csv`. |
| `listar_temas` | Temas pré-configurados (IRPJ, CSLL, PIS/COFINS, IRPF, IRRF, IPI, IOF, SIMPLES, IBS/CBS, preços de transferência, tributação internacional, previdenciárias, processo fiscal, CTN, ICMS/ISS). |
| `status_indice` / `sincronizar_catalogo` | Estado do catálogo local e atualização em segundo plano. |

Numeração: na CF, os artigos do ADCT ficam separados (`"art. 76 do ADCT"`). Em decretos que aprovam um regulamento anexo (RIR/2018, RPS, CLT), `"art. 258"` é do regulamento e `"decreto, art. 1"` é do próprio decreto. Notas de rodapé e índices depois da assinatura não viram artigos.

As citações são interpretadas como um advogado escreveria: `Lei 9.430/96`, `Lei nº 12.973, de 13 de maio de 2014`, `LC 214/2025`, `Decreto 9.580/2018`, `RIR/2018`, `CTN`, `CF`, `MP 2.158-35/2001`, `DL 1.598/77`, `EC 132/2023`.

---

## Instalação

Requer Python 3.10 ou superior. Funciona com o SDK `mcp` 1.8+ e 2.x.

```bash
pip install git+https://github.com/gdexkarma/mcp-planalto
# ou, sem instalar nada globalmente, com uv:
uvx --from git+https://github.com/gdexkarma/mcp-planalto mcp-planalto --help
```

### Claude Code

```bash
claude mcp add planalto -- uvx --from git+https://github.com/gdexkarma/mcp-planalto mcp-planalto
```

### Claude Desktop

Em `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "planalto": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/gdexkarma/mcp-planalto", "mcp-planalto"]
    }
  }
}
```

Se instalou com `pip`, use `"command": "mcp-planalto"` e `"args": []`.

### Servidor HTTP (uso remoto ou por várias pessoas)

```bash
mcp-planalto servir --http --host 0.0.0.0 --porta 8000   # endpoint em /mcp
```

> O modo HTTP não tem autenticação: qualquer pessoa que alcance a porta pode usar todas as ferramentas, inclusive disparar sincronizações. Exponha-o só em rede confiável ou atrás de um proxy com autenticação.

---

## Primeira execução e atualização

Na primeira vez que o servidor sobe, ele monta sozinho, em segundo plano, o **catálogo local** a partir dos quadros do Planalto: cerca de 55 mil normas (leis desde 1891, decretos, decretos-leis, MPs, LCs e ECs), em poucos minutos. Enquanto isso, ler e consultar normas já funciona, porque cada norma também pode ser localizada sob demanda.

A cada início, se a última leitura tiver mais de 6 horas, o servidor relê os quadros do ano corrente. `novidades_legislativas` sempre relê na hora.

### Carga de detalhes do Senado (`--detalhes`)

O catálogo básico traz número, data, ementa e link de cada norma. A carga de detalhes acrescenta, para cada lei, LC, decreto-lei, MP, EC e lei delegada, duas informações do Senado:

- **indexação temática**: termos atribuídos por documentalistas, como "LUCRO REAL" ou "JUROS SOBRE O CAPITAL PRÓPRIO". A ementa de lei tributária costuma ser genérica ("Altera a legislação tributária federal…"); sem a indexação, essas leis não aparecem numa busca por assunto;
- **grafo de alterações**: quem alterou quem, por dispositivo.

```bash
mcp-planalto sincronizar --detalhes                                # leis, LCs, DLs, MPs, ECs
mcp-planalto sincronizar --detalhes --tipos DEC --desde-ano 2000   # decretos, se quiser
```

É retomável: pode ser interrompida (Ctrl+C) e continuada depois, recomeçando de onde parou. Roda na sua máquina, consulta só a API pública do Senado e **não consome tokens do Claude**.

| Precisa do `--detalhes` | Funciona sem ele |
|---|---|
| `buscar_normas` por assunto mais completo (sem ele, a busca só vê ementa e apelido; com ele, também os descritores do Senado) | `ler_norma`, `estrutura_norma` |
| camada "relacionada" do `mapear_tema` e contagens por tema | `consultar_norma`, `historico_alteracoes`, `verificar_atualizacao` (buscam no Senado só a norma consultada, na hora) |
| `novidades_legislativas` com tema rápido (sem ele, até 60 consultas ao Senado por chamada, ~30 s) | camada "alteradora" do `mapear_tema` (consulta só as normas-núcleo) |

Depois da primeira carga, rodar de novo (por exemplo, uma vez por semana) processa só as normas que entraram no catálogo desde então, em poucos minutos.

### Espaço em disco e tempo

Medido em outubro de 2026, com 54.668 normas no catálogo. A linha do `--detalhes` foi extrapolada de uma amostra aleatória de 400 das 20.996 normas que ele processa (2,97 normas/s; cerca de 11 relações de alteração por norma).

| Etapa | Tempo | Banco (`legislacao.db`) | Cache (`cache/`) |
|---|---|---|---|
| Catálogo básico (automático na primeira execução) | ~4 min | 54 MB | 15 MB |
| `--detalhes` (leis, LCs, DLs, MPs, ECs) | ~2 a 3 h | +76 MB | +60 MB |
| `--detalhes --tipos DEC --desde-ano 2000` (estimativa, não medida) | ~1 h | +35 MB | +30 MB |
| **Total típico (catálogo + detalhes, sem decretos)** | | **~130 MB** | **~75 MB** |

O cache cresce também com as normas lidas: cada página do Planalto fica guardada comprimida, de poucos KB até ~350 KB (a LC 214/2025, de 5,4 MB, ocupa 345 KB). Apagar a pasta `cache/` libera espaço sem perder nada; as páginas são baixadas de novo quando necessário.

Os dados ficam em `~/.mcp-planalto` (ou em `MCP_PLANALTO_HOME`): catálogo SQLite, cache HTTP e planilhas exportadas.

---

## Mapeamento temático: como funciona

`mapear_tema("IRPJ")` combina três camadas de evidência:

1. **Núcleo**: normas estruturantes do tema (para IRPJ: DL 1.598/77, Leis 8.981/95, 9.249/95, 9.430/96, 9.532/97, 12.973/14, 14.596/23, RIR/2018 etc.). Você pode acrescentar normas com `normas_extras`. Normas que tratam de vários assuntos entram com **escopo por dispositivo**, por exemplo `"Lei 9.430/1996, arts. 18 a 24-C"` para preços de transferência; só alterações nesses artigos contam para o tema.
2. **Grafo de alterações** (Senado): toda norma que alterou, revogou ou regulamentou um dispositivo do núcleo. É isso que pega as leis de ementa genérica, do tipo "Altera a Lei nº 9.430, de 1996, e dá outras providências", que uma busca por palavra-chave perderia. Com `profundidade=2`, segue também quem alterou as principais alteradoras, mas só fica quem tem termos do tema na ementa ou na indexação (as alteradoras costumam ser leis de vários assuntos). Com escopo por dispositivo, só contam alterações em artigos do escopo; registros do Senado sem dispositivo legível ficam de fora, salvo revogação da norma inteira ou regulamento cuja ementa cite artigo do escopo.
3. **Catálogo**: normas cuja ementa, apelido ou indexação do Senado contém os termos do tema ("lucro real", "lucro presumido", "juros sobre o capital próprio"…). Você pode acrescentar termos com `termos_extras`.

Cada norma recebe uma camada (núcleo, alteradora/regulamentadora, relacionada e, com `profundidade=2`, alteradora de 2º nível), uma relevância (soma das evidências, com o melhor termo do catálogo contando uma vez; usada só para ordenar dentro da camada) e os motivos de ter entrado. Reedições de MP anteriores a 2001 são agrupadas na última edição, e MPs convertidas aparecem dentro da lei de conversão. Temas podem ser combinados (`"IRPJ/CSLL"`). A planilha traz a lista completa, com link para o texto no Planalto e uma aba de notas metodológicas.

Para um tema que não está pré-configurado, passe texto livre e, de preferência, algumas normas-núcleo:

```
mapear_tema("subvenções para investimento", normas_extras=["Lei 14.789/2023", "Lei 12.973/2014"],
            termos_extras=['"subvencao para investimento"', '"subvencoes"'])
```

---

## Linha de comando

Tudo que o servidor faz também está disponível no terminal:

```bash
mcp-planalto ler "Lei 9.430/96" -d "art. 74, § 12"
mcp-planalto ler "LC 214/2025" --estrutura
mcp-planalto ler "DL 1.598/77" -d "art. 12" --historico
mcp-planalto ler "RIR/2018" -t "juros sobre o capital próprio"
mcp-planalto ficha "Lei 12.973/2014"
mcp-planalto historico "Lei 9.430/96" -d "art. 74"
mcp-planalto verificar "Lei 9.249/95"
mcp-planalto buscar '"lucro presumido"' --tipos LEI LCP --desde-ano 2010
mcp-planalto novidades --dias 15 --tema IRPJ
mcp-planalto mapear IRPJ --formato xlsx
mcp-planalto temas
mcp-planalto status
```

---

## Exemplos de perguntas ao assistente

- "Qual a redação atual do art. 74 da Lei 9.430/96? Quem alterou o § 12 e quando?"
- "O texto do Planalto da Lei 9.249/95 está atualizado? Confira antes de responder."
- "O que foi publicado de legislação federal sobre IRPJ e CSLL nos últimos 15 dias?"
- "Mapeie todo o acervo de legislação de PIS/Cofins desde 2002 e gere a planilha."
- "Compare a redação original e a atual do art. 12 do DL 1.598/77."

---

## Configuração

| Variável | Padrão | Efeito |
|---|---|---|
| `MCP_PLANALTO_HOME` | `~/.mcp-planalto` | Diretório de dados. |
| `MCP_PLANALTO_CACHE_HORAS` | `24` | Por quanto tempo uma página de texto é reutilizada sem perguntar ao Planalto se mudou (depois disso o servidor revalida com ETag/Last-Modified, o que é barato). |
| `MCP_PLANALTO_AUTO_SYNC` | `1` | `0` desliga a sincronização automática ao iniciar. |

---

## Skill para o Claude (dispensável)

O roteiro de uso (ler o dispositivo e o contexto, tratar os alertas, conferir a vigência da lei alteradora,
`verificar_atualizacao` antes de concluir, como refazer a verificação quando o usuário contesta) está nas
instruções do próprio servidor, que o Claude recebe ao conectar o MCP. A pasta `skill/legislacao-planalto-mcp/`
traz o mesmo roteiro em forma de skill, mas os testes não mostraram ganho com ela
([docs/benchmark.md](docs/benchmark.md), 3ª rodada); não é preciso instalá-la.

---

## Limites conhecidos

- **Defasagem do compilado.** O Planalto às vezes leva dias para consolidar uma alteração. Use `verificar_atualizacao`, que aponta as normas alteradoras ainda não refletidas no texto.
- **A indexação do Senado também tem defasagem.** Normas publicadas há poucos dias podem ainda não ter vides. `verificar_atualizacao` cobre esse intervalo procurando, no catálogo, normas recentes cuja ementa cita a norma.
- **Vigência diferida.** O Planalto mostra no modo vigente a redação mais nova, mesmo quando ela só vale no futuro. O servidor resolve as notas "(Produção de efeitos)", "(Vigência)" e "(Vide …)" e alerta; quando a cláusula de vigência usa uma fórmula que ele não sabe calcular, o alerta não aparece. Na dúvida, leia a cláusula de vigência da lei alteradora.
- **Regras autônomas.** Uma lei que muda a aplicação de outro dispositivo sem alterar o texto dele (por exemplo, o acréscimo de percentuais de presunção da LC 224/2025 sobre o art. 15 da Lei 9.249/1995) não aparece no texto nem no histórico do dispositivo. Use `historico_alteracoes`, `buscar_normas` e a sua análise.
- **Erros das fontes.** O Planalto às vezes deixa duas redações sem risco (o servidor avisa) ou erra o número da lei na nota; o Senado às vezes registra a alteração no dispositivo ou na norma errada. As respostas dizem de onde vem cada dado para que a conferência seja rápida.
- **Cobertura dos quadros do Planalto.** Leis, LCs, ECs e MPs posteriores a 1988 estão completas (conferido contra o Senado: 99,4% das leis de 1988 a 2026, 100% nos anos recentes). Os quadros do Planalto são parciais para decretos anteriores a 1991 e decretos-leis de 1937 a 1946; normas assim podem ser consultadas no Senado (ficha e histórico), mas nem sempre há texto no Planalto.
- **Escopo.** O servidor cobre leis, LCs, decretos, decretos-leis, MPs, ECs e a Constituição. Instruções Normativas, Soluções de Consulta e demais atos da RFB não estão no Planalto. Decretos não numerados e decretos legislativos ficam de fora.
- **HTML heterogêneo.** Páginas antigas do Planalto têm marcação irregular. O leitor foi testado em normas de várias épocas (CC/2002, CTN, DL 1.598/77, Lei 9.430/96, LC 214/2025, RIR/2018), mas uma página fora do padrão pode exigir ajuste. Nesse caso, `modo="historico"` mostra o texto bruto com as redações riscadas.
- **Uso responsável.** O cliente HTTP limita a frequência de requisições por portal e usa cache. Não reduza esses intervalos.

---

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest              # testes offline (fixtures)
pytest -m live      # testes contra os portais reais
```

Estrutura:

```
src/mcp_planalto/
  server.py              ferramentas MCP
  servico.py             orquestra catálogo, Planalto e Senado
  cli.py                 linha de comando
  db.py                  SQLite + FTS5 (catálogo e grafo de alterações)
  temas.py               temas tributários pré-configurados
  referencias.py         interpretação de citações e de dispositivos
  exportar.py            XLSX/CSV
  http.py                cache, revalidação, limite de taxa e reintentos
  fontes/planalto_indices.py   quadros de legislação do Planalto
  fontes/planalto_texto.py     leitura do texto compilado (vigente × histórico, dispositivos)
  fontes/senado.py             API de Dados Abertos do Senado
```
