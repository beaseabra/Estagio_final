# ===== config.py =====

# =========================
# MODELOS — ESTRATÉGIA HÍBRIDA CIRÚRGICA
#
# Princípio: usar o modelo mais leve que consegue fazer a tarefa com fiabilidade.
#
# GPU 4GB VRAM:
#   llama3.2:3b               → ~2.0 GB VRAM  (rápido, tarefa simples)
#   llama3.1:8b-instruct-q4   → ~5.0 GB VRAM  (lento, tarefa complexa)
#   qwen2.5:7b                → ~4.5 GB VRAM  (já instalado, mantido no planner)
#   nomic-embed-text          → ~0.3 GB VRAM  (sempre leve)
#
# NOTA: llama3.1:8b e qwen2.5:7b não podem correr em simultâneo em 4GB.
# O pipeline é sequencial por isso não há conflito — mas não actives paralelismo
# entre generator_objects e planner no feedback_loop.py.
# =========================

MODELS = {
    # ─────────────────────────────────────────────
    # TAREFAS SIMPLES — Llama 3.2 3B mantido
    # Justificação: output é uma string curta ou JSON plano de 1 nível.
    #   router: devolve apenas "CREATE_SYSTEM" / "UPDATE_SCHEMA" / "CHAT"
    #   generator_relations: lista de {from, to, type} — estrutura trivial
    #   generator_actions: lista de ações com steps — o few-shot resolve alucinações
    #   generator_workspaces: perfis de acesso — JSON moderado, prompt tem format:"json"
    # ─────────────────────────────────────────────
    "router":               "llama3.2:3b",
    "generator_relations":  "llama3.2:3b",
    "generator_actions":    "llama3.2:3b",
    "generator_workspaces": "llama3.2:3b",

    # ─────────────────────────────────────────────
    # TAREFAS COMPLEXAS — Llama 3.1 8B (substitui o 3B nestas roles)
    # Justificação: são as duas roles responsáveis por 100% dos erros no pipeline.log.
    #
    #   generator_objects: tem de gerar 6-10 campos por entidade com tipos corretos,
    #     PKs canónicas, campos de auditoria e entidades de suporte — o 3B alucina
    #     campos nulos, tipos inválidos e schemas truncados a meio.
    #
    #   update_schema: diff cirúrgico onde confundir Object com Workspace destrói
    #     a integridade referencial do schema inteiro. O 8B com few-shot é determinístico.
    # ─────────────────────────────────────────────
    "generator_objects":    "llama3.1:8b-instruct-q4_K_M",
    "update_schema":        "llama3.1:8b-instruct-q4_K_M",

    # ─────────────────────────────────────────────
    # PLANNER — Qwen 2.5 7B mantido
    # Já estava a funcionar bem. Não se muda o que funciona.
    # ─────────────────────────────────────────────
    "planner":              "qwen2.5:7b",

    # ─────────────────────────────────────────────
    # EMBEDDINGS — sem alteração
    # ─────────────────────────────────────────────
    "embeddings":           "nomic-embed-text",
}


# =========================
# ENDPOINTS
# =========================

OLLAMA_URL    = "http://localhost:11434/api/generate"
EMBEDDING_URL = "http://localhost:11434/api/embed"


# =========================
# OPÇÕES LLM — POR MODELO
#
# OPTIONS é o perfil padrão (3B).
# OPTIONS_8B é usado explicitamente pelos handlers que chamam o 8B.
# =========================

# Perfil padrão — Llama 3.2 3B (GPU 4GB)
OPTIONS = {
    "temperature": 0.3,
    "num_ctx":     2048,   # limite seguro de contexto para 4GB
    "num_predict": 800,
}

# Perfil para o 8B — pode usar mais contexto porque é a única tarefa pesada ativa
OPTIONS_8B = {
    "temperature": 0.1,    # mais determinístico para diffs cirúrgicos
    "num_ctx":     3072,   # o 8B aguenta mais contexto sem OOM em 4GB
    "num_predict": 1024,
}


# =========================
# RETRIES
# =========================

MAX_RETRIES = 1


# =========================
# CACHE
# =========================

CACHE_SIMILARITY_THRESHOLD = 0.95


# =========================
# TIPOS VÁLIDOS
# =========================

VALID_FIELD_TYPES = [
    "string",
    "integer",
    "float",
    "boolean",
    "date",
    "datetime",
    "text",
]


# =========================
# PATHS
# =========================

PATHS = {
    "schemas":     "schemas/",
    "logs":        "logs/",
    "database":    "database/",
    "cache_file":  "database/cache.json",
    "pipeline_log":"pipeline.log",
}


# =========================
# SCORE
# =========================

SCORE_THRESHOLD = 60


# =========================
# DEBUG
# =========================

DEBUG            = True
VERBOSE_LOGGING  = True


# =========================
# COMANDO DE INSTALAÇÃO
# (corre uma vez antes de arrancar o servidor)
#
# ollama pull llama3.1:8b-instruct-q4_K_M
#
# Verifica se o modelo está disponível:
# ollama list
# =========================
