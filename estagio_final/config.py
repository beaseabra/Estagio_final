# ===== config.py =====

# =========================
# MODELOS (🔥 MODO HÍBRIDO EQUILIBRADO)
# =========================

MODELS = {
    # ⚡ Tarefas rápidas, relacionamentos, workspaces e ações (Llama 3B)
    "router": "llama3.2:3b",
    "generator_objects": "llama3.2:3b",     # 🔄 Voltou para o Llama 3B para evitar timeouts de VRAM
    "generator_relations": "llama3.2:3b",
    "generator_workspaces": "llama3.2:3b",
    "generator_actions": "llama3.2:3b",

    # 🧠 O "Cérebro" da Arquitetura e Estrutura de Negócio (Qwen 7B)
    "planner": "qwen2.5:7b",                # 🔥 Mantemos o Qwen aqui para planos impecáveis

    "embeddings": "nomic-embed-text"
}


# =========================
# ENDPOINTS
# =========================

OLLAMA_URL = "http://localhost:11434/api/generate"
EMBEDDING_URL = "http://localhost:11434/api/embed"


# =========================
# OPÇÕES LLM
# =========================

OPTIONS = {
    "temperature": 0.3,

    # Limite seguro de contexto para a GPU de 4GB
    "num_ctx": 2048,

    # Limite de tokens para respostas rápidas e diretas
    "num_predict": 800
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
    "text"
]


# =========================
# PATHS
# =========================

PATHS = {
    "schemas": "schemas/",
    "logs": "logs/",
    "database": "database/",
    "cache_file": "database/cache.json",
    "pipeline_log": "pipeline.log"
}


# =========================
# SCORE
# =========================

SCORE_THRESHOLD = 60


# =========================
# DEBUG
# =========================

DEBUG = True
VERBOSE_LOGGING = True
