# Controle do Corte

Sistema web do setor de corte da JR Ferragens & Madeiras. O PostgreSQL é a fonte oficial depois da importação. A planilha de serviços cria ou atualiza pedidos e serviços; o PDF Carrinho complementa um pedido existente por igualdade exata entre **Código Interno** e **Pedido/Nota**. Um pedido pode ter vários serviços. A planilha histórica fica em uma área isolada para revisão.

## Requisitos

- Python 3.12 ou 3.13
- PostgreSQL no Neon, com conexão TLS
- Acesso à internet do servidor de aplicação para o Neon

## Instalação

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
```

Edite **somente** a cópia `.env` com a URL fornecida pelo painel do Neon:

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require&channel_binding=require
APP_MAX_UPLOAD_MB=20
APP_TIMEZONE=America/Sao_Paulo
```

Não envie `.env`, senhas ou URLs reais ao Git. Em hospedagem Streamlit, configure `DATABASE_URL` no gerenciador de secrets da plataforma ou em `.streamlit/secrets.toml` local. O código aceita ambas as opções. O pool SQLAlchemy usa `pool_pre_ping` e reciclagem periódica.

Sem `DATABASE_URL`, o Alembic interrompe a execução. Ele não cria um banco local por engano.

## Preparar banco e iniciar

```powershell
.venv\Scripts\alembic.exe upgrade head
.venv\Scripts\python.exe -m db.check
.venv\Scripts\python.exe -m db.seed --name "Administrador" --email "admin@empresa.com"
.venv\Scripts\streamlit.exe run app.py
```

O comando `db.seed` pede a senha de modo interativo. São necessários ao menos 12 caracteres e, pelo limite do bcrypt, no máximo 72 bytes em UTF-8. Se o banco estiver vazio, a interface mostra como criar o administrador e não libera acesso anônimo. Tabelas são criadas **apenas pelas migrations**, nunca ao abrir o Streamlit.

No Streamlit Community Cloud, após executar as migrations uma vez, configure também estes Secrets de nível raiz para criar o primeiro administrador automaticamente:

```toml
DATABASE_URL = "postgresql+psycopg://USUARIO:SENHA@HOST/BANCO?sslmode=require"
INITIAL_ADMIN_NAME = "Administrador"
INITIAL_ADMIN_EMAIL = "seu-email@empresa.com"
INITIAL_ADMIN_PASSWORD = "uma-senha-forte-de-12-ou-mais-caracteres"
```

O sistema só utiliza essas três entradas enquanto não existe usuário. Depois do primeiro login, remova `INITIAL_ADMIN_PASSWORD` dos Secrets; a senha permanece apenas como hash bcrypt no banco. Nunca envie a senha em conversas nem a salve no Git.

No Linux, use `python -m venv .venv`, `pip install -r requirements.txt`, `alembic upgrade head`, `python -m db.seed ...` e `streamlit run app.py`.

## Fluxo de importação

1. Entre com perfil **ADMIN** ou **GESTOR** e abra **Importações**.
2. Selecione o Excel geral. O parser identifica os cabeçalhos, mesmo se a aba ou o nome do arquivo mudar. Revise a prévia, os erros e os avisos. Confirme para gravar. Cada linha válida vira um serviço; o Código Interno cria ou localiza o pedido.
3. Selecione um ou vários PDFs Carrinho. Revise Pedido/Nota, Carrinho e número de itens. A confirmação processa cada arquivo em sua própria transação. O PDF só vincula automaticamente por código exato normalizado.
4. Consulte o pedido pelo código, serviço, carrinho ou cliente. A ficha separa os dados da planilha, do PDF e da operação, e mostra documentos e auditoria.

O conteúdo original de cada arquivo importado é guardado em `documentos_importados.original_bytes`, com hash SHA-256. Repetir arquivo idêntico não duplica dados. Uma nova versão do Excel atualiza apenas campos pertencentes ao serviço, preservando campos operacionais. Nessa atualização, uma coluna ausente ou um valor inválido preserva o dado anterior; uma célula vazia em coluna presente limpa o campo da planilha e gera auditoria. Erros de linha ficam vinculados ao documento. Um PDF sem pedido vai para **Pendências**; perfis autorizados podem vinculá-lo manualmente com confirmação. Se a planilha chegar depois, o sistema tenta a igualdade exata novamente.

O parser do Carrinho lê primeiro o texto selecionável e os rótulos do documento; usa a posição do texto apenas como fallback para variantes de layout. Não executa OCR.
PDFs digitalizados sem texto são registrados com erro de leitura no histórico, sem criar uma falsa pendência de vínculo.

O arquivo real de serviços tem uma linha com Código Interno composto (`173091 173167`). Ela recebe aviso e registro de revisão; o sistema não cria um pedido fictício nem escolhe um dos dois números sozinho. O PDF pode conter diferença de centavos entre produto e quantidade × preço; o valor impresso é preservado e o aviso fica registrado. PDF digitalizado sem texto selecionável requer OCR futuro.

O resumo do PDF informa uma quantidade total de 681 unidades misturadas (chapas, metros e serviços). Esse número é preservado como dado do PDF e não representa peças cortadas.

## Produção e administração

- **Fila de produção:** busca, status, métricas por pedido e registro de eventos com confirmação. As ações são validadas na camada de serviço.
- **Planejamento:** data, turno, máquina, prioridade e prévia da capacidade. O gestor ou administrador pode autorizar excesso.
- **Painel:** indicadores de pedidos, status e capacidade configurada.
- **Pendências:** PDFs sem vínculo, pedidos sem carrinho, falta de material e atrasos.
- **Administração:** usuários, perfis, ativação, máquinas, capacidade por data/processo/métrica/máquina/turno e limite de upload.
- **Relatórios:** filtros por período, status, máquina, cidade, vendedor, cliente, linha, loja, modalidade e central; visão diária/semanal/mensal, tempos e capacidade. Exportação da página ou de todos os resultados filtrados (até 20.000 linhas) em CSV/XLSX.
- **Histórico legado:** exige mapeamento explícito da coluna `27`; guarda registros históricos em tabela separada, com vínculo exato quando possível e sinalização de revisão. Nunca altera o status operacional atual por inferência.

## Estrutura

- `app.py` e `ui/`: navegação e apresentação Streamlit.
- `db/models.py`: entidades relacionais; `db/repositories/`: consultas filtradas.
- `db/migrations/`: esquema Alembic versionado.
- `importers/`: leitura e normalização de Excel, PDF e legado, sem escrita no banco.
- `services/`: transações e regras de autenticação, importação, planejamento e produção.
- `tests/`: testes de parser e integração com os arquivos fornecidos, se estiverem nos caminhos originais.

IDs internos são UUID. Dinheiro usa `Decimal`/`Numeric`. Dados brutos relevantes ficam em JSONB no PostgreSQL; campos consultáveis permanecem em colunas próprias. Senhas são hashes bcrypt. As alterações de campos importados, status e planejamento geram auditoria.

## Testes

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Os testes de integração usam SQLite temporário para exercitar a lógica sem credenciais. Os testes com os arquivos reais são pulados quando esses arquivos não estão disponíveis. Antes de liberar em produção, execute as migrations contra sua instância Neon e valide login, Excel e PDF pelo aplicativo. A execução local em SQLite não substitui uma validação de rede, permissões e latência no Neon.

## Implantação e backup

Configure as variáveis no servidor, instale dependências, execute `alembic upgrade head` na etapa de release, crie o administrador uma única vez e inicie `streamlit run app.py --server.address 0.0.0.0`. Use HTTPS no proxy ou na plataforma de hospedagem. Não execute migrations a cada rerun do Streamlit.

No Neon, mantenha backups/snapshots conforme a política da empresa. Antes de uma alteração de esquema, crie um snapshot/branch ou exporte via `pg_dump` com a URL **direta**, sem `-pooler`, conforme a [documentação do Neon](https://neon.com/docs/import/migrate-from-neon). Teste a restauração periodicamente; os arquivos originais guardados no banco também fazem parte do backup.

## Limites conhecidos

Esta entrega foi testada com os arquivos reais no banco local, mas a URL do Neon não estava disponível neste ambiente; a conexão à instância final precisa ser validada após configurar `DATABASE_URL`. O parser PDF não executa OCR. A migração histórica é deliberadamente isolada para impedir que conflitos do arquivo antigo alterem pedidos operacionais sem revisão.
