# Controle do Corte

Sistema Streamlit para gestao do setor de corte da JR Ferragens & Madeiras.
O PostgreSQL/Neon e a fonte oficial dos dados depois da importacao.

## Requisitos

- Python 3.12 ou 3.13
- PostgreSQL no Neon com TLS
- Streamlit Community Cloud ou ambiente equivalente

## Configuracao

Crie o ambiente local e instale as dependencias:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
```

No `.env` local:

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require&channel_binding=require
APP_MAX_UPLOAD_MB=20
APP_TIMEZONE=America/Sao_Paulo
```

No Streamlit Cloud, use somente este Secret para abrir o app sem login:

```toml
DATABASE_URL = "postgresql+psycopg://USUARIO:SENHA@HOST/BANCO?sslmode=require&channel_binding=require"
```

A URL copiada do Neon geralmente comeca com `postgresql://`; neste app, troque para `postgresql+psycopg://`.
Nao salve URLs reais, senhas, `.env` ou `.streamlit/secrets.toml` no Git.

## Banco

Prepare o schema antes de abrir a aplicacao:

```powershell
.venv\Scripts\alembic.exe upgrade head
.venv\Scripts\python.exe -m db.check
```

As tabelas sao criadas apenas pelas migrations. O Streamlit nao cria nem altera schema ao abrir.

## Rodar

```powershell
.venv\Scripts\streamlit.exe run app.py
```

Por padrao, o app abre sem login, com permissao operacional completa para facilitar a implantacao inicial.
Para ativar login depois, defina:

```toml
AUTH_ENABLED = "true"
```

Depois crie o primeiro administrador localmente:

```powershell
.venv\Scripts\python.exe -m db.seed --name "Administrador" --email "admin@empresa.com"
```

Tambem e possivel criar o primeiro admin no Cloud com `INITIAL_ADMIN_EMAIL` e `INITIAL_ADMIN_PASSWORD`, mas isso so e usado quando `AUTH_ENABLED = "true"`.

## Fluxo principal

1. Importe a planilha geral de servicos.
2. Importe um ou mais PDFs Carrinho.
3. O sistema vincula PDF ao pedido por igualdade exata entre Codigo Interno e Pedido/Nota.
4. Consulte pedidos por codigo, servico, carrinho, cliente ou filtros operacionais.
5. Acompanhe producao, planejamento, pendencias e relatorios.

O conteudo original de cada arquivo importado fica salvo em `documentos_importados.original_bytes`, com hash SHA-256. Reimportar arquivo identico nao duplica dados. PDF sem pedido correspondente fica em pendencias para vinculacao manual.

## Testes

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Os testes usam SQLite temporario para validar a logica sem credenciais reais. Antes de uso operacional, valide tambem o banco Neon configurado em `DATABASE_URL`.

## Estrutura

- `app.py` e `ui/`: telas Streamlit.
- `db/models.py`: modelos relacionais.
- `db/migrations/`: migrations Alembic.
- `importers/`: leitura de Excel, PDF e historico.
- `services/`: regras de importacao, producao, planejamento, material, usuarios e auditoria.
- `tests/`: testes de parsing, servicos e smoke tests do app.
