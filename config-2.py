# ===== config.py =====

MODEL_TIER = "3b"   

_MODELS_3B = {
    "router":               "llama3.2:3b",
    "generator_objects":    "llama3.2:3b",
    "generator_relations":  "llama3.2:3b",
    "generator_workspaces": "llama3.2:3b",
    "generator_actions":    "llama3.2:3b",
    "update_schema":        "llama3.2:3b",
    "planner":              "qwen2.5:7b",
    "embeddings":           "nomic-embed-text",
}

_MODELS_8B = {
    "router":               "llama3.2:3b",
    "generator_objects":    "llama3.1:8b-instruct-q4_K_M",
    "generator_relations":  "llama3.2:3b",
    "generator_workspaces": "llama3.2:3b",
    "generator_actions":    "llama3.2:3b",
    "update_schema":        "llama3.1:8b-instruct-q4_K_M",
    "planner":              "qwen2.5:7b",
    "embeddings":           "nomic-embed-text",
}

MODELS = _MODELS_8B if MODEL_TIER == "8b" else _MODELS_3B

OLLAMA_URL    = "http://localhost:11434/api/generate"
EMBEDDING_URL = "http://localhost:11434/api/embed"

OPTIONS = {
    "temperature": 0.3,
    "num_ctx":     2048,
    "num_predict": 800,
}

OPTIONS_8B = {
    "temperature": 0.1,
    "num_ctx":     3072,
    "num_predict": 1024,
}

MAX_RETRIES = 1
CACHE_SIMILARITY_THRESHOLD = 0.95

VALID_FIELD_TYPES = [
    "string", "integer", "float", "boolean", "date", "datetime", "text",
]

PATHS = {
    "schemas":      "schemas/",
    "logs":         "logs/",
    "database":     "database/",
    "cache_file":   "database/cache.json",
    "pipeline_log": "pipeline.log",
}

SCORE_THRESHOLD  = 60
DEBUG            = True
VERBOSE_LOGGING  = True
