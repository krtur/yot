# YT Prospector

Descobre canais do YouTube por nicho e exporta, de forma estruturada, os dados públicos de cada perfil e os contatos que o próprio criador publicou. Usa só a YouTube Data API v3 oficial, sem scraping.

## O que ele faz

- Busca por nichos ou palavras-chave (vários de uma vez) e por perfis específicos: `@handle`, link do canal, link de um vídeo ou ID `UC...`.
- Filtra por faixa de inscritos, atividade recente, região, idioma, data de publicação e teto de canais.
- Monta a ficha de cada canal: inscritos, visualizações, número de vídeos, país, idioma, data de criação, tópicos do YouTube, palavras-chave declaradas, médias dos vídeos recentes (visualizações, curtidas, comentários), engajamento, último upload e intervalo entre uploads.
- Contatos, se você ligar a opção: e-mail, WhatsApp, telefone, Instagram, TikTok, X, Facebook, LinkedIn, Kwai, Threads, Telegram, Discord, Twitch, link na bio e site.
- Exporta Excel (abas Canais, Contatos, Nichos e Resumo), CSV e JSON, e grava no Supabase se você quiser.
- Tem interface web (Streamlit) e linha de comando.

## Regras do jogo

A ferramenta foi desenhada em cima das Políticas para Desenvolvedores da API do YouTube. Vale conhecer antes de usar:

- **Contatos.** As políticas proíbem coletar ou guardar dados que identifiquem usuários sem consentimento, inclusive e-mail e telefone, e as penalidades vão de redução de cota a revogação da chave e até encerramento da conta Google. Por isso a extração vem desligada e, quando ligada, lê só o que o criador publicou para contato: a descrição do canal e links repetidos em 2 ou mais vídeos recentes. Links que aparecem em um vídeo só (em geral de patrocinadores) ficam de fora.
- **E-mail da aba Sobre.** Fica atrás de um captcha e não vem pela API. A planilha traz o link "Abrir" e uma coluna amarela para anotar o e-mail depois de conferir manualmente. Não automatize essa etapa: os Termos do YouTube proíbem scraping.
- **Sem pontuações.** As políticas não permitem criar métricas próprias, como nota de canal, ranking entre canais ou inferência de categoria e monetização. A ferramenta mostra dados da API e contas simples (somas, médias e divisões) e ordena por inscritos.
- **Retenção de 30 dias.** Dados obtidos pela API precisam ser atualizados ou apagados em até 30 dias. Rode a busca de novo para atualizar e descarte planilhas antigas. O script do Supabase traz um job opcional de limpeza.
- **Atribuição.** Planilha, CSV e JSON indicam "Fonte: YouTube Data API v3".
- **LGPD.** Contato publicado continua sendo dado pessoal. Use para a finalidade com que foi publicado (contato comercial), identifique-se na primeira mensagem e respeite quem pedir para não ser contatado.

## Instalação

Requisito: Python 3.10 ou mais novo.

```bash
cd yt-prospector
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS e Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
```

### Chave da API

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/) e crie um projeto.
2. Em **APIs e serviços > Biblioteca**, ative a **YouTube Data API v3**.
3. Em **APIs e serviços > Credenciais**, clique em **Criar credenciais > Chave de API**.
4. Edite a chave e, em **Restrições de API**, deixe só a YouTube Data API v3.
5. Cole a chave em `YOUTUBE_API_KEY` no arquivo `.env`.

A API não cobra pelo uso dentro da cota diária.

## Como usar

### Interface web

```bash
streamlit run app.py
```

Informe os nichos (um por linha) e, se quiser, perfis específicos. Ajuste os filtros na barra lateral, marque "Coletar contatos publicados pelo criador" se precisar deles e clique em **Buscar canais**. O resultado aparece em abas, com botões para baixar Excel, CSV e JSON. A barra lateral mostra quanto da cota do dia já foi usado.

### Linha de comando

```bash
# Quanto de cota a busca pode gastar, sem chamar a API
python cli.py -n "finanças pessoais" -n "investimentos" --paginas 3 --estimar

# Busca com contatos: 10 mil a 500 mil inscritos, com upload nos últimos 90 dias
python cli.py -n "finanças pessoais" -n "investimentos" --paginas 2 \
  --min-inscritos 10k --max-inscritos 500k --ativo-dias 90 --contatos

# Nichos de um arquivo, só canais com contato, em Excel e CSV
python cli.py --arquivo-nichos nichos-exemplo.txt --contatos --somente-com-contato --formatos xlsx,csv

# Perfis específicos (entram no relatório mesmo fora dos filtros)
python cli.py -p @canal -p https://www.youtube.com/@outro -p https://youtu.be/ID_DO_VIDEO --contatos
```

Os arquivos vão para a pasta `saida/`. `python cli.py -h` lista todas as opções. As principais:

| Opção | Para que serve |
|---|---|
| `-n`, `--arquivo-nichos` | Nichos ou palavras-chave, direto ou de um `.txt` |
| `-p`, `--arquivo-perfis` | Perfis específicos, direto ou de um `.txt` |
| `--modo` | `videos` (padrão) acha quem publica sobre o tema; `canais` busca pelo nome do canal |
| `--paginas` | Páginas de até 50 resultados por nicho (1 a 10) |
| `--regiao`, `--idioma` | Padrão `BR` e `pt`; use `todas` / `todos` para não filtrar |
| `--ordem` | `relevancia`, `data`, `visualizacoes` ou `avaliacao` |
| `--publicado-apos` | Data mínima de publicação, `AAAA-MM-DD` ou `DD/MM/AAAA` |
| `--min-inscritos`, `--max-inscritos` | Aceita `10000`, `10k`, `10mil`, `1,5M` |
| `--ativo-dias` | Só canais com upload nos últimos N dias |
| `--max-canais` | Teto de canais analisados; entram primeiro os que mais apareceram |
| `--videos-recentes` | Vídeos lidos por canal para as médias (padrão 10) |
| `--contatos`, `--somente-com-contato` | Liga a extração de contatos e, opcionalmente, filtra |
| `--formatos` | `xlsx`, `csv`, `json` (padrão `xlsx,json`) |
| `--supabase` | Grava também no Supabase |

## Cota da API

A cota padrão de um projeto novo é de 100 buscas por dia, mais 10.000 unidades por dia para as demais chamadas. Ela renova à meia-noite no horário do Pacífico (4h ou 5h em Brasília, conforme o horário de verão dos EUA).

| Operação | Custo |
|---|---|
| Cada página de busca (até 50 resultados) | 1 das 100 buscas do dia |
| Carregar até 50 canais | 1 unidade |
| Localizar um `@handle` | 1 unidade |
| Listar os vídeos recentes de um canal | 1 unidade |
| Estatísticas de até 50 vídeos | 1 unidade |

Exemplo: 5 nichos com 2 páginas cada gastam 10 buscas. Se aparecerem 300 canais, são cerca de 6 unidades para carregá-los, 300 para listar os vídeos e 60 para as estatísticas: perto de 370 das 10.000 unidades. Na prática, as 100 buscas diárias são o gargalo.

- As respostas ficam 24 horas em cache (`.cache/youtube.sqlite`). Repetir a mesma busca não gasta cota.
- Um contador local impede ultrapassar o limite. Se a cota acabar no meio, a coleta para e entrega o resultado parcial com um aviso.
- O contador é desta máquina. Outros sistemas usando o mesmo projeto do Google consomem a mesma cota.
- Se o seu projeto tiver cota extra aprovada pelo Google, ajuste `YTP_MAX_BUSCAS_DIA` e `YTP_MAX_UNIDADES_DIA` no `.env`.

## Dicas de busca

- O modo padrão (vídeos sobre o nicho) encontra quem produz conteúdo sobre o tema mesmo que o nome do canal não cite o termo.
- Termos específicos rendem mais que genéricos: "planilha de gastos" traz canais mais certeiros que "finanças".
- Rodar o mesmo nicho com `--ordem data` destaca quem publicou recentemente.
- As colunas "Encontrado por" e "Ocorrências na busca" mostram quem aparece em vários termos.
- Inscritos vêm arredondados pela própria API (123.456 aparece como 123.000).

## O que sai na planilha

- **Canais**: uma linha por canal, com links. As colunas amarelas são da equipe: e-mail comercial verificado, status (lista com Novo, Contatado, Respondeu, Em negociação, Fechado e Descartado), responsável e observações.
- **Contatos**: um contato por linha, com o lugar onde foi encontrado. Só aparece com a extração de contatos ligada.
- **Nichos**: canais por nicho, palavras-chave declaradas pelos canais e tópicos do YouTube.
- **Resumo**: parâmetros da busca, resultado, filtrados, avisos, cota usada, como ler as colunas e regras de uso.

O CSV repete a aba Canais (UTF-8 com BOM, abre certo no Excel). O JSON traz tudo, com chaves em inglês iguais às colunas do Supabase.

## Supabase (opcional)

1. No SQL Editor do projeto, rode `sql/supabase_schema.sql`.
2. No `.env`, preencha `SUPABASE_URL` e `SUPABASE_SECRET_KEY` (a secret key, nunca a publishable/anon).
3. Use `--supabase` na linha de comando ou o botão "Enviar para o Supabase" que aparece depois da busca na interface.

O envio faz upsert por `channel_id`. As colunas da equipe (`verified_business_email`, `status`, `assignee`, `notes`) nunca são enviadas, então o que foi anotado no Supabase é preservado. A tabela tem RLS ligado e nenhuma política, ou seja, só a secret key acessa. O script traz, comentado, um job do `pg_cron` que apaga canais não atualizados há mais de 30 dias.

## Limitações

- O e-mail da aba Sobre não vem pela API; a conferência é manual.
- A busca do YouTube devolve uma amostra dos resultados mais relevantes, não a lista completa. Mais termos e mais páginas ampliam a cobertura.
- Os contatos dependem do que o criador escreveu. Formatos muito criativos podem escapar.
- Links antigos no formato `/c/Nome` nem sempre são localizáveis pela API. Prefira o `@handle` ou o link de um vídeo do canal.

## Testes

```bash
pytest
```

São 66 testes com uma API falsa: não usam rede nem cota.

## Estrutura

```
yt-prospector/
├── app.py                  interface web (Streamlit)
├── cli.py                  linha de comando
├── prospector/
│   ├── youtube.py          cliente da API: cache, cota e novas tentativas
│   ├── pipeline.py         busca → canais → vídeos → registros
│   ├── contacts.py         extração de contatos publicados
│   ├── export.py           Excel, CSV e JSON
│   └── supabase_sink.py    envio opcional ao Supabase
├── sql/supabase_schema.sql tabela, índices, RLS e retenção
├── tests/                  API falsa e testes
├── nichos-exemplo.txt
├── .env.example
└── requirements.txt
```
