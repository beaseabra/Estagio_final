# AiBizCore Intelligence — Instructions

Este documento explica como colocar a arquitetura a funcionar localmente e descreve a função dos principais ficheiros, para facilitar manutenção, testes e alterações futuras.

---

## Índice

1. [Visão geral](#1-visão-geral)
2. [Pré-requisitos](#2-pré-requisitos)
3. [Preparar o Ollama](#3-preparar-o-ollama)
4. [Preparar o backend](#4-preparar-o-backend)
5. [Iniciar o backend](#5-iniciar-o-backend)
6. [Preparar o frontend](#6-preparar-o-frontend)
7. [Teste rápido da aplicação](#7-teste-rápido-da-aplicação)
8. [Limpar cache e histórico](#8-limpar-cache-e-histórico)
9. [Integração com SQL Server](#9-integração-com-sql-server)
10. [Integração com a framework AIBIZCore](#10-integração-com-a-framework-aibizcore)
11. [Principais ficheiros](#11-principais-ficheiros)
12. [Como modificar funcionalidades comuns](#12-como-modificar-funcionalidades-comuns)
13. [Problemas frequentes](#13-problemas-frequentes)
14. [Prompts recomendados para teste](#14-prompts-recomendados-para-teste)
15. [Ordem recomendada para demonstração](#15-ordem-recomendada-para-demonstração)
16. [Notas de segurança](#16-notas-de-segurança)

---

## 1. Visão geral

O **AiBizCore Intelligence** transforma pedidos escritos em linguagem natural em blueprints técnicos para a plataforma **AIBIZCore**.

Fluxo geral:

1. O utilizador escreve um pedido no frontend.
2. O backend recebe o pedido através da API.
3. O router classifica a intenção do pedido, por exemplo:
   - `CREATE_SYSTEM`
   - `CREATE_OBJECT`
   - `CREATE_WORKSPACE`
   - `UPDATE_SCHEMA`
   - `CHAT`
4. O sistema verifica se existe uma resposta semelhante em cache.
5. Se necessário, são chamados modelos locais através do Ollama.
6. O blueprint é validado e normalizado.
7. O resultado é devolvido ao frontend.
8. Opcionalmente, o blueprint pode ser convertido em:
   - plano SQL Server;
   - plano de metadata para a framework AIBIZCore.

A arquitetura foi pensada para correr localmente, com LLMs via Ollama, evitando depender de APIs externas para a geração principal.

---

## 2. Pré-requisitos

Antes de iniciar o projeto, confirmar que estão instalados:

- Python 3.10 ou superior;
- Node.js e npm;
- Ollama;
- Git;
- SQL Server, apenas se for necessário testar a integração real com base de dados.

Confirmar versões:

```bash
python --version
node --version
npm --version
ollama --version
```

---

## 3. Preparar o Ollama

Iniciar o Ollama, se ainda não estiver ativo:

```bash
ollama serve
```

Noutro terminal, instalar os modelos usados pela arquitetura:

```bash
ollama pull qwen2.5:7b
ollama pull llama3.2:3b
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

Modelos usados normalmente:

| Modelo | Função |
|---|---|
| `qwen2.5:7b` | Planner e tarefas mais estruturadas |
| `llama3.2:3b` | Geração mais leve e rápida |
| `llama3.1:8b` | Geração alternativa mais robusta |
| `nomic-embed-text` | Embeddings para cache semântica |

Testar se o Ollama responde:

```bash
ollama run llama3.2:3b
```

Depois de confirmar, sair com:

```text
/bye
```

---

## 4. Preparar o backend

Abrir um terminal na pasta principal do projeto:

```bash
cd AiBizCore_v4
```

Criar ambiente virtual.

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Instalar dependências:

```bash
pip install -r requirements.txt
```

Se o projeto estiver a usar `uv`, também pode ser usado:

```bash
uv pip install -r requirements.txt
```

Criar ou confirmar o ficheiro `.env` na raiz do backend/projeto. Exemplo sem credenciais reais:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
OLLAMA_PLANNER_MODEL=qwen2.5:7b
OLLAMA_GENERATOR_MODEL=llama3.2:3b
EMBEDDING_MODEL=nomic-embed-text

# Execução SQL real — manter desativado por defeito
ENABLE_SQL_EXECUTION=false

# Execução de metadata real na framework — manter desativado por defeito
ENABLE_FRAMEWORK_EXECUTION=false

# Configuração SQL Server — preencher apenas em ambiente seguro
DB_SERVER=<servidor>
DB_DATABASE=<base_de_dados>
DB_USERNAME=<utilizador>
DB_PASSWORD=<password>
DB_DRIVER=ODBC Driver 17 for SQL Server
```

> Não colocar passwords, connection strings reais ou ficheiros `.env` em commits, relatório ou vídeo.

---

## 5. Iniciar o backend

Na pasta onde está o ficheiro `api.py`, executar:

```bash
uvicorn api:app --reload --host 127.0.0.1 --port 8000
```

Se o ficheiro principal tiver outro nome, adaptar o comando:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

O backend deverá ficar disponível em:

```text
http://127.0.0.1:8000
```

Endpoints principais:

| Método | Endpoint | Função |
|---|---|---|
| `POST` | `/api/prompt` | Processar prompts do utilizador |
| `GET` | `/api/get_schema` | Obter schema atual |
| `POST` | `/api/save_cache` | Guardar resultado em cache |
| `POST` | `/api/regression_tests` | Executar testes de regressão |
| `POST` | `/api/db-preview` | Gerar preview da BD |
| `POST` | `/api/db-plan` | Gerar plano SQL em dry-run |
| `POST` | `/api/db-test-connection` | Testar ligação à BD |
| `GET` | `/api/db-config-status` | Ver estado da configuração da BD |
| `POST` | `/api/db-execute` | Executar SQL real, se permitido |
| `POST` | `/api/framework-plan` | Gerar plano de metadata da framework |
| `POST` | `/api/framework-preflight` | Validar plano da framework antes da execução |
| `POST` | `/api/framework-execute` | Executar metadata real, se permitido |

---

## 6. Preparar o frontend

Abrir outro terminal e entrar na pasta do frontend. Dependendo da estrutura do projeto, será uma destas opções:

```bash
cd frontend
```

ou:

```bash
cd client
```

Instalar dependências:

```bash
npm install
```

Iniciar o frontend:

```bash
npm run dev
```

Abrir no browser o URL indicado pelo Vite/React, normalmente:

```text
http://localhost:5173
```

Se o frontend usar outro porto, seguir o endereço mostrado no terminal.

---

## 7. Teste rápido da aplicação

Com backend, frontend e Ollama ativos, testar no frontend um pedido simples:

```text
Cria apenas um objeto chamado Teste Video Produto. Campos: codigo string, nome string, preco number, dataCriacao date.
```

Resultado esperado:

- O router deve classificar como `CREATE_OBJECT`.
- O backend deve gerar um blueprint com um único objeto.
- O frontend deve apresentar o objeto com os campos indicados.

Depois testar um sistema completo:

```text
Cria um sistema de gestão de encomendas para uma loja online. O sistema deve ter clientes, produtos, encomendas, pagamentos e entregas. Cada cliente pode fazer várias encomendas, cada encomenda pode ter vários produtos, cada encomenda tem um pagamento associado e pode ter uma entrega. Cria também workspaces e ações principais para gerir o sistema.
```

Resultado esperado:

- O router deve classificar como `CREATE_SYSTEM`.
- O blueprint deve incluir objetos, campos, relações, workspaces e ações.

---

## 8. Limpar cache e histórico

Se a aplicação estiver a devolver respostas antigas ou comportamentos inesperados, limpar a cache local.

Na pasta principal do projeto:

```bash
rm -f database/cache.json
rm -f database/prompt_history.json
mkdir -p database
echo '{}' > database/cache.json
echo '[]' > database/prompt_history.json
```

Em Windows PowerShell:

```powershell
Remove-Item database/cache.json -ErrorAction SilentlyContinue
Remove-Item database/prompt_history.json -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force database
'{}' | Set-Content database/cache.json
'[]' | Set-Content database/prompt_history.json
```

Depois reiniciar o backend e, se necessário, limpar também o estado no frontend através do botão de limpar/reset.

---

## 9. Integração com SQL Server

A integração SQL Server deve ser testada primeiro em modo seguro.

Passos normais no frontend:

1. Criar ou carregar um blueprint.
2. Clicar em **Preview BD**.
3. Confirmar a tabela, chave primária, campos do utilizador e campos de sistema.
4. Clicar em **Preparar Plano**.
5. Ver o plano em modo **dry-run**.
6. Só usar **Executar no SQL Server** num ambiente controlado.

A execução real deve ficar bloqueada por defeito. Para permitir execução, é necessário:

- configurar corretamente a ligação à BD;
- ativar `ENABLE_SQL_EXECUTION=true` no servidor;
- enviar a frase de confirmação exigida pela API;
- confirmar que o plano foi validado.

Frase de confirmação usada normalmente:

```text
EXECUTE_SQL_SERVER
```

Em caso de erro durante a execução real, o sistema deve fazer rollback.

---

## 10. Integração com a framework AIBIZCore

A integração com a framework cria metadata lógica para a plataforma. Esta parte é diferente da criação física da tabela SQL.

Passos normais no frontend:

1. Criar ou carregar um blueprint.
2. Gerar o plano da framework.
3. Executar o preflight.
4. Confirmar que não existem conflitos.
5. Só executar a metadata em ambiente seguro.
6. Validar no AIBIZCore em **Definições → Objeto**.

A metadata pode incluir:

| Elemento | Função |
|---|---|
| `CSYSObject` | Objeto lógico |
| `CSYSObjectField` | Campos do objeto |
| `CSYSView` | Vista/listagem |
| `CSYSAction` | Ações associadas |
| `CSYSObjectAction` | Ligação entre objeto e ações |
| `CSYSPermission` | Permissões |
| Workspaces e ligações associadas | Organização da utilização, quando aplicável |

A execução real deve ficar bloqueada por defeito. Para permitir execução, é necessário:

- ativar `ENABLE_FRAMEWORK_EXECUTION=true`;
- usar a frase de confirmação correta;
- passar no preflight;
- garantir que não há conflitos com objetos, tabelas ou metadata já existentes.

Frase de confirmação usada normalmente:

```text
EXECUTE_FRAMEWORK_METADATA
```

---

## 11. Principais ficheiros

> Os nomes podem variar ligeiramente conforme a versão do projeto. Esta secção descreve a organização lógica da arquitetura.

### Backend principal

| Ficheiro | Função |
|---|---|
| `api.py` | Define a API FastAPI, expõe os endpoints usados pelo frontend e recebe prompts, schemas e pedidos de integração. |
| `router.py` | Classifica a intenção do pedido: `CREATE_SYSTEM`, `CREATE_OBJECT`, `CREATE_WORKSPACE`, `UPDATE_SCHEMA` ou `CHAT`. |
| `main.py` | Pode funcionar como orquestrador principal. Chama o router e encaminha o pedido para o handler correto. |

### Handlers de geração

| Ficheiro | Função |
|---|---|
| `create_system_handler.py` | Trata pedidos de criação de sistemas completos, coordenando objetos, relações, workspaces e ações. |
| `create_object_handler.py` | Trata pedidos de criação de um único objeto com os seus campos. Útil para integração simples com SQL Server e framework. |
| `update_schema_handler.py` | Trata pedidos incrementais sobre um blueprint existente, como adicionar campo, renomear objeto, criar relação ou remover elemento. |
| `planner.py` | Constrói o plano intermédio a partir do pedido do utilizador. Normalmente usa um modelo mais forte/estruturado. |

### Generators

| Ficheiro | Função |
|---|---|
| `object_generator.py` | Gera objetos e campos. |
| `relation_generator.py` | Gera relações entre objetos. |
| `workspace_generator.py` | Gera workspaces, perfis ou áreas de utilização. |
| `action_generator.py` | Gera ações de negócio e ações básicas, como criar, listar, atualizar, arquivar, aprovar ou cancelar. |

### Validação, normalização e qualidade

| Ficheiro | Função |
|---|---|
| `canonical_schema.py` | Define ou normaliza o formato canónico do blueprint. Afeta frontend, validação, SQL e framework. |
| `models.py` / `schema_models.py` | Define modelos Pydantic usados para validar a estrutura dos dados. |
| `validator.py` | Valida o blueprint depois da geração, podendo corrigir ou rejeitar estruturas inválidas. |
| `semantic_rules.py` | Contém regras determinísticas e semânticas para tipos, nomes, relações e ações. |
| `evaluator.py` | Avalia a qualidade do blueprint e pode calcular score ou problemas de consistência. |

### Cache e armazenamento

| Ficheiro | Função |
|---|---|
| `cache.py` / `semantic_cache.py` | Gere cache semântica com embeddings para reutilizar resultados de pedidos semelhantes. |
| `storage.py` | Guarda histórico, schemas, cache ou resultados intermédios. |
| `database/cache.json` | Guarda respostas em cache. Pode ser apagado em caso de respostas antigas ou inconsistentes. |
| `database/prompt_history.json` | Guarda histórico de prompts. Pode ser apagado durante testes ou antes de uma demonstração. |

### SQL Server

| Ficheiro | Função |
|---|---|
| `sql_adapter.py` / `db_adapter.py` | Converte o blueprint em estrutura SQL Server, incluindo tabelas, colunas, tipos, chaves e campos de sistema. |
| `db_preview.py` | Gera pré-visualização da estrutura SQL. Não deve executar alterações reais. |
| `db_plan.py` | Gera plano SQL em modo dry-run. Mostra o que seria executado sem alterar a base de dados. |
| `db_executor.py` | Executa alterações reais em SQL Server. Deve manter confirmação, variável de ambiente e rollback. |

### Framework AIBIZCore

| Ficheiro | Função |
|---|---|
| `framework_object_planner.py` | Gera o plano de metadata para a framework AIBIZCore. |
| `framework_metadata_preflight.py` | Valida o plano antes de executar metadata real, verificando conflitos e condições necessárias. |
| `framework_metadata_executor.py` | Executa a criação ou atualização de metadata na framework. Deve respeitar confirmação explícita e variável de ambiente. |

### Testes

| Ficheiro | Função |
|---|---|
| `regression_tests.py` | Executa testes de regressão com prompts esperados. Útil para confirmar se alterações não partiram funcionalidades antigas. |

### Frontend

| Ficheiro | Função |
|---|---|
| `frontend/src/App.jsx` ou `frontend/src/App.tsx` | Componente principal do frontend. Liga chat, estado global, canvas e chamadas à API. |
| `frontend/src/components/*` | Componentes visuais: chat, canvas, cartões, botões, painéis de BD/framework, etc. |
| `frontend/src/services/api.js` ou `api.ts` | Centraliza chamadas do frontend para o backend. |
| `frontend/src/components/Canvas*` ou `Blueprint*` | Renderiza o blueprint visual. |

### Configuração

| Ficheiro | Função |
|---|---|
| `.env` | Guarda configuração local e variáveis sensíveis. Não deve ser partilhado publicamente. |
| `requirements.txt` | Lista dependências Python do backend. |
| `package.json` | Lista dependências e scripts do frontend. |

---

## 12. Como modificar funcionalidades comuns

### Alterar o modelo LLM usado

1. Editar `.env`.
2. Mudar `OLLAMA_MODEL`, `OLLAMA_PLANNER_MODEL` ou `OLLAMA_GENERATOR_MODEL`.
3. Reiniciar o backend.

### Alterar a classificação dos pedidos

1. Editar `router.py`.
2. Adicionar ou ajustar regras para `CREATE_SYSTEM`, `CREATE_OBJECT`, `UPDATE_SCHEMA`, etc.
3. Testar com prompts curtos e longos.

### Melhorar criação de objetos

1. Editar `create_object_handler.py` e `object_generator.py`.
2. Confirmar que o output segue o `canonical_schema`.
3. Testar com prompts de um único objeto.

### Melhorar criação de sistemas completos

1. Editar `create_system_handler.py`, `planner.py` e generators.
2. Verificar se objetos, relações, workspaces e ações continuam consistentes.

### Melhorar edição incremental

1. Editar `update_schema_handler.py`.
2. Adicionar regras determinísticas para operações previsíveis, por exemplo:
   - adicionar campo;
   - renomear objeto;
   - adicionar relação;
   - remover campo.
3. Evitar depender apenas do LLM para JSON diff.

### Alterar validação

1. Editar `validator.py`, `semantic_rules.py` e `schema_models.py`.
2. Correr `regression_tests` depois da alteração.

### Alterar preview SQL

1. Editar `db_preview.py` e `sql_adapter.py`.
2. Confirmar que não existe execução real nesta fase.

### Alterar execução SQL

1. Editar `db_executor.py`.
2. Manter obrigatórios:
   - confirmação;
   - variável de ambiente;
   - validação;
   - rollback.

### Alterar metadata da framework

1. Editar `framework_object_planner.py`, `framework_metadata_preflight.py` e `framework_metadata_executor.py`.
2. Testar primeiro em dry-run/preflight.
3. Confirmar depois em **AIBIZCore → Definições → Objeto**.

### Alterar frontend

1. Editar componentes em `frontend/src/components`.
2. Editar chamadas API em `frontend/src/services`.
3. Confirmar que o backend continua a receber `current_schema` corretamente.

---

## 13. Problemas frequentes

### O backend não arranca

Soluções:

- Confirmar ambiente virtual ativo.
- Confirmar dependências instaladas.
- Confirmar se o ficheiro correto é `api.py` ou `main.py`.
- Verificar se o porto `8000` já está ocupado.

### O frontend não comunica com o backend

Soluções:

- Confirmar que o backend está em `http://127.0.0.1:8000`.
- Confirmar variável de API no frontend, se existir.
- Verificar erros CORS no browser.

### O LLM não responde

Soluções:

- Confirmar que `ollama serve` está ativo.
- Confirmar que o modelo foi instalado com `ollama pull`.
- Testar manualmente com:

```bash
ollama run llama3.2:3b
```

### A aplicação devolve respostas antigas

Soluções:

- Limpar `database/cache.json`.
- Limpar `database/prompt_history.json`.
- Reiniciar backend.
- Limpar estado do frontend.

### `UPDATE_SCHEMA` falha a extrair JSON

Causa provável:

- O LLM respondeu com JSON inválido, comentários ou reticências.

Soluções:

- Usar prompts incrementais mais simples.
- Adicionar regra determinística em `update_schema_handler.py`.
- Melhorar parser/extrator JSON.
- Não depender apenas do LLM para operações previsíveis.

### Execução SQL está bloqueada

Causa provável:

- Proteção de segurança ativa.

Soluções:

- Confirmar `ENABLE_SQL_EXECUTION`.
- Confirmar frase de confirmação.
- Confirmar ligação à base de dados.
- Confirmar plano validado.

### Metadata da framework não executa

Soluções:

- Confirmar `ENABLE_FRAMEWORK_EXECUTION`.
- Executar preflight.
- Confirmar que não existem conflitos.
- Confirmar permissões e ligação correta à base de dados/framework.

---

## 14. Prompts recomendados para teste

### Criar objeto simples

```text
Cria apenas um objeto chamado Teste Video Produto. Campos: codigo string, nome string, preco number, dataCriacao date.
```

### Criar sistema completo

```text
Cria um sistema de gestão de encomendas para uma loja online. O sistema deve ter clientes, produtos, encomendas, pagamentos e entregas. Cada cliente pode fazer várias encomendas, cada encomenda pode ter vários produtos, cada encomenda tem um pagamento associado e pode ter uma entrega. Cria também workspaces e ações principais para gerir o sistema.
```

### Prompt incremental simples

```text
Renomeia o objeto Produto para Artigo.
```

### Prompt incremental alternativo

```text
adiciona campo estado em Encomenda
```

---

## 15. Ordem recomendada para demonstração

1. Abrir Ollama.
2. Iniciar backend.
3. Iniciar frontend.
4. Criar sistema completo.
5. Mostrar blueprint.
6. Fazer uma alteração incremental simples.
7. Criar objeto simples.
8. Mostrar **Preview BD**.
9. Mostrar **Preparar Plano**.
10. Mostrar **Gerar Framework** / preflight.
11. Validar em **AIBIZCore → Definições → Objeto**.

---

## 16. Notas de segurança

- Nunca partilhar o ficheiro `.env`.
- Nunca mostrar passwords ou connection strings em vídeo.
- Não ativar execução real em ambiente de demonstração sem necessidade.
- Preferir preview e dry-run para testes.
- Executar SQL/framework real apenas em ambiente controlado.
- Confirmar sempre o objeto criado na framework através de **Definições → Objeto**.
