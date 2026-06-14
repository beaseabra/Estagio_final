# ===== handlers/update_schema_handler.py =====
# Versão 2.0 — "Parser à Prova de Bala"
# Reescrito para máxima tolerância a falhas do LLM (modelo 3B)
# e mutação granular de qualquer elemento do schema.

import json
import re
import unicodedata
import requests
from typing import Any, Dict, List, Optional, Tuple, Union

from config import MODELS, OPTIONS, OLLAMA_URL


# ─────────────────────────────────────────────
# SECÇÃO 1 — UTILITÁRIOS DE NORMALIZAÇÃO
# ─────────────────────────────────────────────

def _normalize(s: str) -> str:
    """Remove acentos, converte para minúsculas, elimina espaços extra."""
    if not s:
        return ""
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", s).lower().strip()


def _names_match(a: str, b: str) -> bool:
    """Correspondência fuzzy: exacta ou subcadeia."""
    if not a or not b:
        return False
    na, nb = _normalize(a), _normalize(b)
    return na == nb or na in nb or nb in na


# ─────────────────────────────────────────────
# SECÇÃO 2 — REPARAÇÃO DEFENSIVA DO JSON DO LLM
# ─────────────────────────────────────────────

def _sanitize_llm_output(raw: str) -> Optional[Dict]:
    """
    Tenta extrair um JSON válido da resposta do LLM.
    Tolera: markdown fences, texto antes/depois, JSON truncado.
    """
    if not raw:
        return None

    # 1. Remover blocos de markdown
    raw = re.sub(r"```(?:json)?", "", raw).strip()

    # 2. Extrair o bloco JSON mais externo (tolerante a texto antes/depois)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None

    candidate = match.group()

    # 3. Tentar parse directo
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 4. Reparação de vírgulas a mais antes de '}'  ou ']'
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 5. Última tentativa: truncar no último objecto completo
    last_brace = candidate.rfind("}")
    if last_brace > 0:
        try:
            return json.loads(candidate[: last_brace + 1])
        except json.JSONDecodeError:
            pass

    return None


def _ensure_schema_structure(schema: Optional[Dict]) -> Dict:
    """Garante que o schema tem sempre as três chaves raiz."""
    if not schema or not isinstance(schema, dict):
        schema = {}
    schema.setdefault("objects", [])
    schema.setdefault("workspaces", [])
    schema.setdefault("actions", [])
    return schema


# ─────────────────────────────────────────────
# SECÇÃO 3 — VALIDAÇÃO ESTRUTURAL DO DIFF DO LLM
# ─────────────────────────────────────────────

# Chaves de topo de nível que o LLM pode devolver
_VALID_TOP_KEYS = {"objects", "workspaces", "actions"}


def _validate_diff(diff: Dict) -> Tuple[Dict, List[str]]:
    """
    Valida e corrige o diff devolvido pelo LLM.
    Devolve (diff_corrigido, lista_de_avisos).
    
    Principais correcções:
    - Workspaces dentro de objects → mover para workspaces
    - Objects dentro de workspaces → mover para objects
    - Chaves desconhecidas → ignorar
    - Items que não são dicts → ignorar
    """
    warnings: List[str] = []
    clean: Dict[str, List] = {"objects": [], "workspaces": [], "actions": []}

    # Ignorar chaves que não são as três raiz
    unknown_keys = set(diff.keys()) - _VALID_TOP_KEYS
    if unknown_keys:
        warnings.append(f"[diff] Chaves desconhecidas ignoradas: {unknown_keys}")

    # ── Processar cada secção ──────────────────────────────────
    for section in _VALID_TOP_KEYS:
        items = diff.get(section, [])

        # O LLM às vezes devolve um dict em vez de list
        if isinstance(items, dict):
            items = [items]
            warnings.append(f"[diff] '{section}' era dict → convertido para list")

        if not isinstance(items, list):
            warnings.append(f"[diff] '{section}' não é lista → ignorado")
            continue

        for raw_item in items:
            if not isinstance(raw_item, dict):
                warnings.append(f"[diff] Item não-dict em '{section}' ignorado: {raw_item!r}")
                continue

            # ── Detecção de "fusão de contexto" ───────────────
            # Caso 1: um Workspace apareceu dentro de objects
            if section == "objects" and _item_looks_like_workspace(raw_item):
                warnings.append(
                    f"[diff] Workspace '{raw_item.get('name')}' estava em 'objects' → movido para 'workspaces'"
                )
                clean["workspaces"].append(raw_item)
                continue

            # Caso 2: um Object apareceu dentro de workspaces
            if section == "workspaces" and _item_looks_like_object(raw_item):
                warnings.append(
                    f"[diff] Object '{raw_item.get('name')}' estava em 'workspaces' → movido para 'objects'"
                )
                clean["objects"].append(raw_item)
                continue

            clean[section].append(raw_item)

    return clean, warnings


def _item_looks_like_workspace(item: Dict) -> bool:
    """Heurística: tem 'permissions', 'objects' (lista de strings) ou 'primary_entity'."""
    has_permissions = "permissions" in item
    has_str_objects = isinstance(item.get("objects"), list) and all(
        isinstance(o, str) for o in item.get("objects", [])
    )
    has_primary = "primary_entity" in item
    return has_permissions or has_primary or (has_str_objects and "fields" not in item)


def _item_looks_like_object(item: Dict) -> bool:
    """Heurística: tem 'fields' (lista de dicts com 'name'/'type')."""
    fields = item.get("fields", [])
    if not isinstance(fields, list) or not fields:
        return False
    return any(
        isinstance(f, dict) and ("name" in f or "type" in f)
        for f in fields
    )


# ─────────────────────────────────────────────
# SECÇÃO 4 — APLICADORES DE MUTAÇÃO
# ─────────────────────────────────────────────

def _apply_objects_diff(schema: Dict, object_diffs: List[Dict]) -> List[str]:
    """Aplica mutações (rename, type-change, add field, delete field, delete object)."""
    log: List[str] = []

    for item in object_diffs:
        if not isinstance(item, dict):
            continue

        obj_name = item.get("name", "").strip()
        obj_orig = item.get("original_name", obj_name).strip()
        is_delete = item.get("delete", False)

        if not obj_name:
            log.append("[objects] Item sem nome ignorado")
            continue

        # ── DELETE objeto ──────────────────────────────────────
        if is_delete:
            before = len(schema["objects"])
            schema["objects"] = [
                o for o in schema["objects"]
                if not _names_match(o.get("name", ""), obj_orig)
            ]
            if len(schema["objects"]) < before:
                log.append(f"[objects] '{obj_orig}' eliminado")
                # Limpar referências nos workspaces
                for ws in schema["workspaces"]:
                    ws["objects"] = [
                        o for o in ws.get("objects", [])
                        if not _names_match(o, obj_orig)
                    ]
            else:
                log.append(f"[objects] '{obj_orig}' não encontrado para eliminar")
            continue

        # ── Encontrar objecto existente ────────────────────────
        target_obj = next(
            (o for o in schema["objects"] if _names_match(o.get("name", ""), obj_orig)),
            None
        )

        if target_obj is None:
            # Objecto novo: criar
            new_obj = {
                "name": obj_name,
                "fields": _clean_fields(item.get("fields", []))
            }
            schema["objects"].append(new_obj)
            log.append(f"[objects] Novo objecto criado: '{obj_name}'")
            continue

        # ── Rename ────────────────────────────────────────────
        old_name = target_obj["name"]
        if not _names_match(obj_orig, obj_name):
            target_obj["name"] = obj_name
            log.append(f"[objects] '{old_name}' → renomeado para '{obj_name}'")
            # Propagar rename nos workspaces
            for ws in schema["workspaces"]:
                ws["objects"] = [
                    obj_name if _names_match(o, old_name) else o
                    for o in ws.get("objects", [])
                ]
                if _names_match(ws.get("primary_entity", ""), old_name):
                    ws["primary_entity"] = obj_name

        # ── Mutação de campos ──────────────────────────────────
        if "fields" in item:
            _apply_fields_diff(target_obj, item["fields"], log)

    return log


def _apply_fields_diff(obj: Dict, field_diffs: List, log: List[str]) -> None:
    """Aplica mutações granulares aos campos de um objecto."""
    if not isinstance(field_diffs, list):
        return

    existing_fields: List[Dict] = obj.setdefault("fields", [])
    obj_name = obj.get("name", "?")

    for nf in field_diffs:
        if not isinstance(nf, dict):
            continue

        f_name = nf.get("name", "").strip()
        f_orig = nf.get("original_name", f_name).strip()
        is_del = nf.get("delete", False)

        if not f_name:
            continue

        # ── DELETE campo ───────────────────────────────────────
        if is_del:
            before = len(existing_fields)
            obj["fields"] = [
                f for f in existing_fields
                if _normalize(f.get("name", "")) != _normalize(f_orig)
            ]
            existing_fields = obj["fields"]
            if len(existing_fields) < before:
                log.append(f"[fields/{obj_name}] '{f_orig}' eliminado")
            else:
                log.append(f"[fields/{obj_name}] '{f_orig}' não encontrado para eliminar")
            continue

        # ── Encontrar campo existente ──────────────────────────
        target_field = next(
            (f for f in existing_fields if _normalize(f.get("name", "")) == _normalize(f_orig)),
            None
        )

        if target_field is None:
            # Novo campo
            existing_fields.append({
                "name": f_name,
                "type": nf.get("type", "string")
            })
            log.append(f"[fields/{obj_name}] Novo campo '{f_name}' adicionado")
            continue

        # ── Rename campo ───────────────────────────────────────
        if _normalize(f_orig) != _normalize(f_name):
            old_fname = target_field["name"]
            target_field["name"] = f_name
            log.append(f"[fields/{obj_name}] '{old_fname}' → renomeado para '{f_name}'")

        # ── Alterar tipo ───────────────────────────────────────
        if "type" in nf and nf["type"] != target_field.get("type"):
            old_type = target_field.get("type", "?")
            target_field["type"] = nf["type"]
            log.append(f"[fields/{obj_name}] '{f_name}' tipo: '{old_type}' → '{nf['type']}'")


def _apply_workspaces_diff(schema: Dict, ws_diffs: List[Dict]) -> List[str]:
    """Aplica mutações a workspaces (rename, update, delete)."""
    log: List[str] = []

    for item in ws_diffs:
        if not isinstance(item, dict):
            continue

        ws_name = item.get("name", "").strip()
        ws_orig = item.get("original_name", ws_name).strip()
        is_delete = item.get("delete", False)

        if not ws_name:
            log.append("[workspaces] Item sem nome ignorado")
            continue

        # ── DELETE workspace ───────────────────────────────────
        if is_delete:
            before = len(schema["workspaces"])
            schema["workspaces"] = [
                w for w in schema["workspaces"]
                if not _names_match(w.get("name", ""), ws_orig)
            ]
            if len(schema["workspaces"]) < before:
                log.append(f"[workspaces] '{ws_orig}' eliminado")
            else:
                log.append(f"[workspaces] '{ws_orig}' não encontrado para eliminar")
            continue

        # ── Encontrar workspace existente ──────────────────────
        target_ws = next(
            (w for w in schema["workspaces"] if _names_match(w.get("name", ""), ws_orig)),
            None
        )

        if target_ws is None:
            # Workspace novo
            clean_item = {k: v for k, v in item.items() if k not in ("original_name", "delete")}
            schema["workspaces"].append(clean_item)
            log.append(f"[workspaces] Novo workspace criado: '{ws_name}'")
            continue

        # ── Rename ────────────────────────────────────────────
        if not _names_match(ws_orig, ws_name):
            old_ws_name = target_ws["name"]
            target_ws["name"] = ws_name
            log.append(f"[workspaces] '{old_ws_name}' → renomeado para '{ws_name}'")

        # ── Actualizar outros campos (description, color, icon, etc.) ──
        protected_keys = {"name", "original_name", "delete", "objects", "permissions", "actions"}
        for k, v in item.items():
            if k not in protected_keys:
                target_ws[k] = v
                log.append(f"[workspaces/{ws_name}] '{k}' actualizado")

        # ── Actualizar lista de objects (se fornecida) ─────────
        if "objects" in item and isinstance(item["objects"], list):
            target_ws["objects"] = item["objects"]
            log.append(f"[workspaces/{ws_name}] lista de objects actualizada")

        # ── Actualizar permissions (se fornecidas) ─────────────
        if "permissions" in item and isinstance(item["permissions"], list):
            target_ws["permissions"] = item["permissions"]
            log.append(f"[workspaces/{ws_name}] permissions actualizadas")

    return log


def _apply_actions_diff(schema: Dict, action_diffs: List[Dict]) -> List[str]:
    """Aplica mutações a acções (rename, type-change, delete)."""
    log: List[str] = []

    # Filtro de nomes inválidos gerados pelo LLM
    _BAD_NAME_PATTERNS = re.compile(r"^(undefined|add_|null)", re.IGNORECASE)

    for item in action_diffs:
        if not isinstance(item, dict):
            continue

        act_name = item.get("name", "").strip()
        act_orig = item.get("original_name", act_name).strip()
        is_delete = item.get("delete", False)

        if not act_name or _BAD_NAME_PATTERNS.match(act_name):
            log.append(f"[actions] Nome inválido ignorado: '{act_name}'")
            continue

        # ── DELETE acção ───────────────────────────────────────
        if is_delete:
            before = len(schema["actions"])
            schema["actions"] = [
                a for a in schema["actions"]
                if not _names_match(a.get("name", ""), act_orig)
            ]
            if len(schema["actions"]) < before:
                log.append(f"[actions] '{act_orig}' eliminada")
                # Limpar referências em workspaces
                for ws in schema["workspaces"]:
                    if "permissions" in ws:
                        ws["permissions"] = [
                            p for p in ws["permissions"]
                            if not _names_match(p, act_orig)
                        ]
            else:
                log.append(f"[actions] '{act_orig}' não encontrada para eliminar")
            continue

        # ── Encontrar acção existente ──────────────────────────
        target_act = next(
            (a for a in schema["actions"] if _names_match(a.get("name", ""), act_orig)),
            None
        )

        if target_act is None:
            # Acção nova
            new_action = {
                "name": act_name,
                "type": item.get("type", "DOMAIN_ACTION"),
                "description": item.get("description", ""),
                "trigger": item.get("trigger", "manual"),
                "steps": item.get("steps", []),
                "preconditions": item.get("preconditions", []),
                "postconditions": item.get("postconditions", []),
                "entities_involved": item.get("entities_involved", [])
            }
            schema["actions"].append(new_action)
            log.append(f"[actions] Nova acção criada: '{act_name}'")
            continue

        # ── Rename ────────────────────────────────────────────
        if not _names_match(act_orig, act_name):
            old_act_name = target_act["name"]
            target_act["name"] = act_name
            log.append(f"[actions] '{old_act_name}' → renomeada para '{act_name}'")
            # Propagar rename nos workspaces
            for ws in schema["workspaces"]:
                if "permissions" in ws:
                    ws["permissions"] = [
                        act_name if _names_match(p, old_act_name) else p
                        for p in ws["permissions"]
                    ]

        # ── Actualizar tipo ────────────────────────────────────
        if "type" in item and item["type"] != target_act.get("type"):
            old_type = target_act.get("type", "?")
            target_act["type"] = item["type"]
            log.append(f"[actions] '{act_name}' tipo: '{old_type}' → '{item['type']}'")

        # ── Actualizar outros campos opcionais ─────────────────
        for optional_key in ("description", "trigger", "steps", "preconditions", "postconditions"):
            if optional_key in item:
                target_act[optional_key] = item[optional_key]

    return log


# ─────────────────────────────────────────────
# SECÇÃO 5 — AUXILIARES
# ─────────────────────────────────────────────

def _clean_fields(fields: Any) -> List[Dict]:
    """Devolve lista de campos válidos a partir de qualquer input."""
    if not isinstance(fields, list):
        return []
    result = []
    seen = set()
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = f.get("name", "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append({"name": name, "type": f.get("type", "string")})
    return result


def _build_context_string(schema: Dict) -> str:
    """Gera a string de contexto para o prompt do LLM."""
    tables = []
    for obj in schema.get("objects", []):
        field_names = [f.get("name", "") for f in obj.get("fields", []) if isinstance(f, dict)]
        tables.append(f"'{obj.get('name')}' (campos: {', '.join(field_names)})")

    actions = [a.get("name", "") for a in schema.get("actions", []) if isinstance(a, dict)]
    workspaces = [w.get("name", "") for w in schema.get("workspaces", []) if isinstance(w, dict)]

    return (
        f"TABELAS: {' | '.join(tables) or 'Nenhuma'}\n"
        f"AÇÕES: {', '.join(actions) or 'Nenhuma'}\n"
        f"WORKSPACES: {', '.join(workspaces) or 'Nenhum'}"
    )


# ─────────────────────────────────────────────
# SECÇÃO 6 — PROMPT DO LLM
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a JSON patch generator for a software schema editor.

ESTADO ACTUAL DO SISTEMA:
{context}

A tua tarefa: Ler o pedido do utilizador e devolver APENAS as alterações em JSON.

REGRAS OBRIGATÓRIAS:
1. Devolve APENAS os elementos que vão ser modificados, adicionados ou apagados.
2. Para RENOMEAR usa "original_name" (nome actual exacto) + "name" (nome novo).
3. Para APAGAR usa "delete": true com "original_name".
4. Para MUDAR TIPO fornece o novo "type" dentro do campo correcto.
5. NÃO inventes campos que não existem.
6. NÃO mistures Workspaces dentro de "objects" nem Objects dentro de "workspaces".
7. Workspaces são perfis de acesso com permissions. Objects/Tabelas têm fields.

EXEMPLO CORRECTO — "Renomeia campo 'preco' para 'valor' e muda para float na tabela Produto. Apaga a acção Vender.":
{
  "objects": [
    {
      "original_name": "Produto",
      "name": "Produto",
      "fields": [
        { "original_name": "preco", "name": "valor", "type": "float" }
      ]
    }
  ],
  "actions": [
    { "original_name": "Vender", "name": "Vender", "delete": true }
  ]
}

TIPOS VÁLIDOS PARA CAMPOS: string, integer, float, boolean, date, datetime, text
TIPOS VÁLIDOS PARA ACÇÕES: DOMAIN_ACTION, CRUD_ACTION, REPORT_ACTION, NOTIFICATION_ACTION, VALIDATION_ACTION

RESPONDE APENAS COM JSON VÁLIDO. SEM MARKDOWN. SEM TEXTO EXTRA."""


# ─────────────────────────────────────────────
# SECÇÃO 7 — HANDLER PRINCIPAL
# ─────────────────────────────────────────────

def handle_update_schema(
    prompt: str,
    current_schema: Optional[Dict[str, Any]]
) -> Union[Dict[str, Any], str]:
    """
    Handler principal de mutação do schema.
    
    Pipeline:
    1. Validar e normalizar o schema de entrada
    2. Invocar o LLM para gerar o diff JSON
    3. Reparar o JSON do LLM (tolerância a falhas)
    4. Validar estruturalmente o diff (anti-fusão de contexto)
    5. Aplicar mutações granulares em cada secção
    6. Devolver o schema actualizado
    """

    # ── 1. Normalizar schema de entrada ───────────────────────
    schema = _ensure_schema_structure(current_schema)

    # ── 2. Construir e enviar prompt ao LLM ───────────────────
    context_str = _build_context_string(schema)
    full_prompt = _SYSTEM_PROMPT.replace("{context}", context_str)
    full_prompt += f"\n\nPEDIDO DO UTILIZADOR: {prompt}"

    payload = {
        "model": MODELS.get("system", MODELS.get("router", "llama3.2:3b")),
        "prompt": full_prompt,
        "format": "json",
        "stream": False,
        "options": {**OPTIONS, "temperature": 0.0, "num_predict": 2048}
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        raw_text = response.json().get("response", "").strip()
    except requests.RequestException as e:
        return {"success": False, "error": f"Erro de comunicação com o LLM: {e}"}

    print(f"\n[update_schema] 🧠 Resposta bruta do LLM:\n{raw_text}\n")

    # ── 3. Reparar JSON do LLM ────────────────────────────────
    diff_raw = _sanitize_llm_output(raw_text)
    if diff_raw is None:
        return {
            "success": False,
            "error": "O modelo não devolveu JSON válido. Tenta ser mais específico no pedido."
        }

    # ── 4. Validar estrutura do diff ──────────────────────────
    diff, struct_warnings = _validate_diff(diff_raw)
    for w in struct_warnings:
        print(f"[update_schema] ⚠️  {w}")

    # ── 5. Aplicar mutações por secção ────────────────────────
    all_logs: List[str] = []

    if diff.get("objects"):
        all_logs += _apply_objects_diff(schema, diff["objects"])

    if diff.get("workspaces"):
        all_logs += _apply_workspaces_diff(schema, diff["workspaces"])

    # Acções em nível raiz + acções dentro de workspaces do diff
    action_diffs = list(diff.get("actions", []))
    for ws_diff in diff.get("workspaces", []):
        if isinstance(ws_diff.get("actions"), list):
            action_diffs.extend(ws_diff["actions"])

    if action_diffs:
        all_logs += _apply_actions_diff(schema, action_diffs)

    # ── 6. Log de auditoria ───────────────────────────────────
    print(f"[update_schema] ✅ {len(all_logs)} mutação(ões) aplicada(s):")
    for entry in all_logs:
        print(f"   {entry}")

    if not all_logs:
        print("[update_schema] ⚠️  Nenhuma mutação foi aplicada. O LLM pode não ter reconhecido os elementos.")

    return {
        "success": True,
        "type": "SYSTEM",
        "data": schema,
        "mutations_applied": len(all_logs),
        "mutation_log": all_logs,
        "struct_warnings": struct_warnings
    }