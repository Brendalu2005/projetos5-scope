# Camada Preventiva de Priorização Territorial — Conecta Recife

## Preparar o ambiente

Na raiz do projeto, com Python 3.10 ou superior:

```bash
python -m venv .venv
```

Ative o ambiente no Linux/WSL com `source .venv/bin/activate` ou no PowerShell
com `.\.venv\Scripts\Activate.ps1`. Depois instale as dependências:

```bash
python -m pip install -r requirements.txt
```

## Baixar os dados brutos (sprint 1)

```bash
python camada_preventiva/scripts/01_baixar_dados.py
```

O script cria `camada_preventiva/dados/bruto/` e baixa os nove arquivos abaixo.
O destino padrão é calculado a partir do script, independentemente da pasta
em que ele for executado. Não é necessário preencher URLs manualmente.

| Arquivo local | Conjunto oficial do Portal de Dados Abertos do Recife |
| --- | --- |
| `156_2023.csv`, `156_2024.csv`, `156_2025.csv` | [Central de Atendimento de Serviços da EMLURB – 156](https://dados.recife.pe.gov.br/dataset/central-de-atendimento-de-servicos-da-emlurb-156) |
| `bairros.geojson` | [Área urbana — Bairros do Recife](https://dados.recife.pe.gov.br/dataset/area-urbana) |
| `drenagem_elementos.geojson`, `drenagem_rede.geojson`, `setores_coleta.geojson` | [Núcleos urbanizados](https://dados.recife.pe.gov.br/dataset/nucleos-urbanizados) |
| `roteiros_coleta.csv` | [Roteiro de Coleta](https://dados.recife.pe.gov.br/dataset/roteiro-de-coleta) |
| `onde_descartar.csv` | [Destinação de resíduos sólidos — Onde Descartar](https://dados.recife.pe.gov.br/dataset/destinacao-de-residuos-solidos) |

`onde_descartar.csv` é incluído porque o notebook atual exige esse arquivo.
As bases opcionais da Defesa Civil não são baixadas nesta etapa.

O portal possui publicações com nomes iguais para 2024 e 2025. O script fixa os
IDs dos recursos correspondentes aos arquivos usados na execução histórica do
projeto, consultando a API oficial para obter seus endereços atuais. A escolha
não depende da ordem da listagem. Um recurso do portal ainda pode ter seu conteúdo
atualizado: o manifesto registra a versão efetivamente baixada pelo seu SHA-256.

O script valida o formato e a presença de registros; nos CSVs, também confere
as colunas necessárias. Ele reutiliza arquivos existentes válidos e confere o
SHA-256 quando já existe um registro anterior. Arquivos locais sem manifesto
têm sua origem marcada como não verificada. Downloads incompletos ou inválidos
não substituem arquivos anteriores.

O arquivo `dados/bruto/manifesto_downloads.json` registra fontes, IDs, datas,
tamanhos, contagens e SHA-256, além de eventuais falhas. Os dados e o manifesto
ficam fora do Git, conforme o `.gitignore`. Se algum arquivo falhar, o script
continua os demais, termina com código 1 e permite tentar novamente.

Para baixar novamente os recursos selecionados:

```bash
python camada_preventiva/scripts/01_baixar_dados.py --sobrescrever
```

Para usar outra pasta, por exemplo uma pasta compartilhada com o Colab:

```bash
python camada_preventiva/scripts/01_baixar_dados.py --destino /caminho/dados/bruto
```

Nesse caso, ajuste `BRUTO_CUSTOM` no notebook para o mesmo caminho de dados
brutos. Se usar `PASTA_LOCAL`, ela deve apontar para a pasta `camada_preventiva`,
que contém `dados/`. Consulte todas as opções com `--help`.

## Depois do download

Abra `camada_preventiva/notebooks/camada_preventiva.ipynb` e execute as células
em ordem. O script prepara apenas os dados brutos: o notebook gera os arquivos
de `dados/processado/` e `saidas/`. A classificação de situações segue as regras
da etapa 3 abaixo. As outras pendências da sprint 2 ainda precisam ser
concluídas antes de considerar a base tratada pronta.

## Recorte piloto (etapa 2)

O notebook está configurado para **Drenagem na RPA 4**, usando os arquivos de
2023, 2024 e 2025. Na base baixada, esse recorte tem 5.821 registros após a
limpeza de datas, dos quais 3.448 possuem coordenadas válidas. A RPA 4 tem o
maior número de registros georreferenciados de Drenagem entre as seis RPAs,
oferecendo volume para o agrupamento territorial da próxima sprint.

A seleção usa o nome exato da categoria, sem incluir `MACRODRENAGEM` ou outros
grupos por semelhança do nome. O filtro de RPA é aplicado antes das demais
etapas de limpeza, para que contagens e arquivos derivados usem o mesmo piloto.
A RPA é a informada na solicitação; a validação de coordenadas continua usando
o limite do Recife, conforme a lógica original do notebook.

Para mudar o piloto, edite `GRUPOS_RECORTE` (uma categoria) e `RPA_PILOTO`
(um número de 1 a 6) na célula de parâmetros e execute as células em ordem.
O notebook interrompe o processamento se a configuração for inválida ou o
recorte não tiver registros. Os dados brutos continuam completos.

Os dados de 2025 cobrem até 10/09/2025.

## Classificar situações (etapa 3)

A etapa 4 do notebook mantém a classificação simples em `FINAL` e `ABERTA`.
A regra operacional adotada pelo projeto é:

| Situação normalizada | Classificação |
| --- | --- |
| `ATENDIDA` | `FINAL` |
| `CADASTRADA`, `PREPARACAO`, `EXECUCAO`, `FISCALIZACAO`, `PENDENCIA`, `PENDENTE` | `ABERTA` |

As grafias `PREPARAO`, `EXECUO` e `FISCALIZAO` são corrigidas para
`PREPARACAO`, `EXECUCAO` e `FISCALIZACAO`. O valor recebido é preservado em
`SITUACAO_ORIGINAL`, para permitir a conferência com a origem.

O arquivo `camada_preventiva/saidas/situacoes_unicas.csv` registra a frequência
e o tipo de cada situação do recorte. Classificações manuais já preenchidas
nesse arquivo prevalecem sobre o padrão, desde que sejam `FINAL` ou `ABERTA`.
Linhas vazias do CSV são preenchidas pelo padrão apenas para situações conhecidas.

Situações novas não recebem uma classificação presumida: o notebook salva a
lista para revisão e interrompe a execução. Confira o significado na origem,
preencha a coluna `tipo` e execute a etapa 4 novamente. Situações ausentes na
base precisam ser corrigidas na origem. Se duas grafias da mesma situação tiverem
classificações conflitantes, corrija o conflito no CSV antes de continuar.

A célula de exportação também impede salvar a base enquanto houver tipos
vazios ou inválidos, inclusive se a etapa de classificação tiver sido pulada.
O recorte permanece Drenagem na RPA 4; essa classificação não remove registros.

## Sprint 3 — Agrupamento territorial

Execute as novas células depois do fechamento das bases tratadas. A entrada é
`camada_preventiva/dados/processado/156_limpo.parquet`; o recorte permanece
Drenagem/RPA 4. A padronização textual adicional segue adiada.

O parâmetro `RAIO_M = 80` define a distância em metros nas coordenadas projetadas
em EPSG:31985. As denúncias com coordenadas válidas são ordenadas por
`DATA_DEMANDA` e `ID_LINHA`. A primeira ainda não agrupada vira o centro fixo de
um ponto; as denúncias ainda disponíveis dentro do raio pertencem a esse ponto.
O processo se repete até atribuir um ponto a cada denúncia georreferenciada.
Denúncias isoladas também são preservadas, formando pontos de contagem 1.

O centro é uma coordenada de denúncia real e não se desloca com a entrada de
novos membros. O agrupamento não se expande por encadeamento de vizinhos.
Cada denúncia fica a até 80 m do centro; duas denúncias do mesmo ponto podem
estar a até 160 m entre si. Em áreas com raios sobrepostos, a ordem estabelecida
define a atribuição. Os resultados são reproduzíveis para a mesma base e raio;
os IDs dos pontos não são identificadores permanentes entre versões da base.

A tabela por ponto reúne centro, referência da denúncia inicial, bairro/RPA
informados, contagem, primeiras e últimas datas de demanda, última atualização,
contagens abertas/finais e última situação observada. Esta última usa a data de
atualização, com data da demanda e ID como desempate. `SITUACAO_PONTO` indica
`ABERTA` se houver alguma solicitação aberta no grupo; caso contrário, `FINAL`.
É um resumo das solicitações, sem inferir a condição física atual do local.

Saídas:

- `dados/processado/pontos_agrupados.parquet`: centros e histórico resumido, com geometria e CRS.
- `dados/processado/denuncias_pontos.parquet`: denúncias com `ID_PONTO`, centro e distância, preservando seu histórico individual.
- `saidas/pontos_agrupados.csv`: tabela dos pontos para consulta.
- `saidas/validacao_pontos.png`: amostra visual com centros, raios e denúncias.
- `saidas/pontos_validacao.csv`: dados dos pontos selecionados na amostra visual.

Os caminhos acima são relativos a `camada_preventiva/`. Registros sem coordenada
válida continuam em `156_limpo.parquet` e não recebem ponto. A célula de entrada
confere o piloto e as coordenadas; a de exportação confere contagens, vínculo
único de cada denúncia, situações e distância máxima antes de salvar.

Na execução sobre a base atual, 3.448 denúncias geraram 891 pontos, sendo 246
com uma única denúncia. Os outros 2.373 registros do piloto permanecem fora do
agrupamento por não terem coordenadas válidas.

A conferência visual seleciona o ponto mais frequente, o de maior distância ao
centro e um de menor contagem, removendo repetições. Os gráficos usam distâncias
em metros em relação ao centro e mostram os outros grupos próximos como contexto.

## Executar os testes

```bash
python -m unittest discover -s tests -v
```
