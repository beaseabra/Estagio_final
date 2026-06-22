"""
Script de recolha de métricas para a secção "3.3 Resultados e Validação" do relatório.

COMO USAR:
    1. Copia este ficheiro para a pasta raiz do projeto AiBizCore
       (a mesma pasta onde estão main.py, config.py, models.py, etc.)
    2. (Opcional, mas recomendado) Garante que o Ollama está a correr:
           ollama serve
       e que os modelos do config.py estão feitos pull.
    3. Corre:
           python collect_results.py > resultados.txt 2>&1
    4. Manda-me o conteúdo de resultados.txt (ou cola aqui no chat).

As secções 1 e 2 NÃO precisam do Ollama (são determinísticas).
A secção 3 precisa do Ollama — se não estiver acessível, falha de forma
controlada e o resto do script continua.
"""

import time
import traceback


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ──────────────────────────────────────────────────────────────────
# 1. GOLDEN DATASET — testes de regressão (sem Ollama)
# ──────────────────────────────────────────────────────────────────

def run_golden_dataset():
    section("1. GOLDEN DATASET (update_schema_handler) — sem Ollama")
    try:
        from handlers.update_schema_handler import run_regression_tests
    except ImportError:
        from update_schema_handler import run_regression_tests

    results = run_regression_tests()
    sumario = results.pop("_sumario")

    for name, res in results.items():
        status = "PASSOU" if res.get("passou") else "FALHOU"
        print(f"  [{status}] {name}")

    print(f"\n  TOTAL: {sumario['taxa']} testes passados")
    return sumario


# ──────────────────────────────────────────────────────────────────
# 2. COBERTURA DETERMINÍSTICA DO MOTOR DE EDIÇÃO (sem Ollama)
#    Quantos comandos de edição comuns são resolvidos só com regex,
#    sem precisar de cair no fallback do LLM.
# ──────────────────────────────────────────────────────────────────

EDIT_TEST_PROMPTS = [
    "adiciona o campo telefone ao objeto cliente",
    "remove o campo stock do produto",
    "renomeia o campo preco para valor_unitario",
    "muda o tipo do campo quantidade para integer",
    "cria o objeto Fornecedor com os campos nome, email, telefone",
    "renomeia o objeto cliente para utilizador",
    "remove o objeto categoria",
    "cria uma relação entre cliente e encomenda",
    "remove a relação entre cliente e encomenda",
    "cria um workspace Vendas com os objetos cliente, encomenda",
    "remove o workspace Vendas",
    "adiciona o objeto cliente ao workspace Backoffice",
    "cria uma ação AprovarEncomenda para o objeto encomenda",
    "remove a ação AprovarEncomenda",
    "muda o trigger da ação AprovarEncomenda para automated",
    "adiciona o objeto produto à ação AprovarEncomenda",
    "explica-me como funciona o sistema",  # esperado: sem cobertura regex
]

BASE_SCHEMA = {
    "objects": [
        {"name": "Cliente", "fields": [
            {"name": "clienteid", "type": "integer"},
            {"name": "nome", "type": "string"},
        ]},
        {"name": "Produto", "fields": [
            {"name": "produtoid", "type": "integer"},
            {"name": "preco", "type": "float"},
            {"name": "stock", "type": "integer"},
            {"name": "quantidade", "type": "integer"},
        ]},
        {"name": "Categoria", "fields": [
            {"name": "categoriaid", "type": "integer"},
        ]},
    ],
    "relations": [{"from": "Cliente", "to": "Produto", "type": "ONE_TO_MANY"}],
    "workspaces": [{
        "name": "Backoffice", "objects": ["Cliente", "Produto"],
        "primary_entity": "Cliente", "permissions": ["VER"],
    }],
    "actions": [],
}


def run_rule_coverage():
    section("2. COBERTURA DO MOTOR DE EDIÇÃO (regex vs fallback LLM) — sem Ollama")
    try:
        from handlers.update_schema_handler import _rule_based_operations
    except ImportError:
        from update_schema_handler import _rule_based_operations
    from models import parse_blueprint

    resolved = 0
    total = len(EDIT_TEST_PROMPTS)

    for prompt in EDIT_TEST_PROMPTS:
        bp = parse_blueprint(BASE_SCHEMA)
        ops = _rule_based_operations(prompt, bp)
        status = f"regex ({len(ops)} op)" if ops else "precisa LLM"
        if ops:
            resolved += 1
        print(f"  [{status:>16}]  {prompt}")

    print(f"\n  COBERTURA REGEX: {resolved}/{total} pedidos resolvidos sem chamar "
          f"o LLM ({resolved/total*100:.0f}%)")


# ──────────────────────────────────────────────────────────────────
# 3. GERAÇÃO PONTA-A-PONTA (CREATE_SYSTEM) — PRECISA DE OLLAMA
# ──────────────────────────────────────────────────────────────────

CREATE_SYSTEM_PROMPTS = [
    "cria um sistema para gerir uma loja online",
    "cria um sistema de gestão hospitalar",
    "cria um sistema de gestão de uma escola",
    "cria um sistema de gestão financeira para uma pequena empresa",
    "cria um sistema de gestão de uma biblioteca",
]


def run_end_to_end():
    section("3. GERAÇÃO PONTA-A-PONTA (CREATE_SYSTEM) — precisa do Ollama")
    try:
        from handlers.create_system_handler import handle_create_system
    except ImportError:
        from create_system_handler import handle_create_system

    rows = []
    for prompt in CREATE_SYSTEM_PROMPTS:
        print(f"\n  > A processar: '{prompt}'")
        start = time.time()
        try:
            result = handle_create_system(prompt)
        except Exception as e:
            print(f"    ERRO: {e}")
            rows.append({"prompt": prompt, "erro": str(e)})
            continue

        elapsed = round(time.time() - start, 2)
        evaluation = result.get("evaluation", {}) or {}
        score = evaluation.get("score")
        success = result.get("success")
        n_objects = len((result.get("schema") or {}).get("objects", []))

        print(f"    success={success} | score={score} | objetos={n_objects} | tempo={elapsed}s")
        rows.append({
            "prompt": prompt, "success": success, "score": score,
            "objetos": n_objects, "tempo_s": elapsed,
        })

    scores = [r["score"] for r in rows if r.get("score") is not None]
    if scores:
        print(f"\n  SCORE MÉDIO: {sum(scores) / len(scores):.1f}")

    times = [r["tempo_s"] for r in rows if "tempo_s" in r]
    if times:
        print(f"  TEMPO MÉDIO: {sum(times) / len(times):.1f}s")

    return rows


# ──────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_golden_dataset()
    run_rule_coverage()

    try:
        run_end_to_end()
    except Exception:
        print("\n[AVISO] A geração ponta-a-ponta falhou — confirma que o Ollama está "
              "a correr ('ollama serve') e que os modelos do config.py estão "
              "feitos pull (ollama pull <modelo>). Detalhe do erro:")
        traceback.print_exc()

    print("\n\nManda-me o conteúdo deste output (ou o ficheiro resultados.txt) "
          "para passarmos os números para a secção 3.3 do relatório.")
