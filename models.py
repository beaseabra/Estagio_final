# ===== models.py =====
# Camada de armadura Pydantic v2 — AiBizCore
#
# PROPÓSITO: Absorver todo o output errático do Llama 3B (null, tipos errados,
# chaves inexistentes, listas que são dicts, contexto fundido) ANTES de chegar
# ao aggregator, evaluator ou qualquer handler.
#
# REGRA DE OURO: Nenhum componente downstream toca em dicts crus da IA.
# Toda a validação e coerção acontece aqui, uma vez só.
#
# RETROCOMPATIBILIDADE: BlueprintModel.to_dict() devolve o formato exacto
# que o pipeline legado (cache, storage, frontend, validator) espera.

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS INTERNOS — usados pelos validators e pelo aggregator
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_list(v: Any) -> list:
    """
    Transforma qualquer valor numa lista segura.
      None        → []
      dict        → [dict]   (LLM devolve objecto singular em vez de lista)
      str vazia   → []
      list        → list     (passa directo)
      outro       → []
    """
    if v is None:
        return []
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return v
    return []


def _safe_str(v: Any, default: str = "") -> str:
    """Garante string mesmo que o LLM devolva null ou número."""
    if v is None:
        return default
    return str(v).strip()


# Tipos de campo aceites pelo sistema
VALID_FIELD_TYPES = {
    "string", "integer", "float", "boolean",
    "date", "datetime", "text",
}

# Aliases que o LLM usa mas o sistema não — normalizados na entrada
_FIELD_TYPE_ALIASES: dict[str, str] = {
    "int": "integer", "number": "integer", "num": "integer",
    "bigint": "integer", "smallint": "integer",
    "str": "string", "varchar": "string", "char": "string", "nvarchar": "string",
    "bool": "boolean", "flag": "boolean",
    "timestamp": "datetime", "datetime64": "datetime", "datetimetz": "datetime",
    "decimal": "float", "double": "float", "real": "float", "numeric": "float",
    "longtext": "text", "clob": "text",
}

VALID_RELATION_TYPES = {"ONE_TO_MANY", "MANY_TO_MANY"}

VALID_ACTION_TYPES = {
    "DOMAIN_ACTION", "CRUD_ACTION", "REPORT_ACTION",
    "NOTIFICATION_ACTION", "VALIDATION_ACTION",
    "INTEGRATION_ACTION", "AUTOMATED_ACTION",
    # legado — mantidos para retrocompatibilidade com schemas existentes
    "CREATE_OBJECT", "CREATE_RELATION", "ASSIGN_TO_WORKSPACE",
    # português vindo do generator_actions
    "AÇÃO DE DOMÍNIO", "OPERAÇÃO BÁSICA",
}

VALID_TRIGGERS = {"manual", "automated", "scheduled", "automatizado", "agendado"}


# ─────────────────────────────────────────────────────────────────────────────
# FIELD
# ─────────────────────────────────────────────────────────────────────────────

class FieldModel(BaseModel):
    name: str = Field(default="")
    type: str = Field(default="string")

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, v: Any) -> str:
        s = _safe_str(v).lower().replace(" ", "_").replace("-", "_")
        # remover caracteres inválidos mantendo apenas alfanuméricos e underscore
        import re
        return re.sub(r"[^a-z0-9_]", "", s)

    @field_validator("type", mode="before")
    @classmethod
    def clean_type(cls, v: Any) -> str:
        raw = _safe_str(v, "string").lower().strip()
        normalized = _FIELD_TYPE_ALIASES.get(raw, raw)
        return normalized if normalized in VALID_FIELD_TYPES else "string"

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# OBJECT
# ─────────────────────────────────────────────────────────────────────────────

class ObjectModel(BaseModel):
    name: str = Field(default="")
    fields: List[FieldModel] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, v: Any) -> str:
        return _safe_str(v)

    @field_validator("fields", mode="before")
    @classmethod
    def coerce_fields(cls, v: Any) -> list:
        raw = _coerce_list(v)
        # filtrar itens que não são dicts ou que não têm nome
        return [item for item in raw if isinstance(item, dict) and item.get("name")]

    @model_validator(mode="after")
    def ensure_primary_key(self) -> "ObjectModel":
        """Garante que existe sempre uma PK canónica (nomeobjectoid)."""
        if not self.name:
            return self
        pk_name = self.name.lower() + "id"
        # remover caracteres inválidos do pk_name (mesmo que ObjectModel.name tenha espaços)
        import re
        pk_name = re.sub(r"[^a-z0-9_]", "", pk_name)
        if not any(f.name == pk_name for f in self.fields):
            self.fields.insert(0, FieldModel(name=pk_name, type="integer"))
        return self

    @model_validator(mode="after")
    def deduplicate_fields(self) -> "ObjectModel":
        seen: set[str] = set()
        unique: List[FieldModel] = []
        for f in self.fields:
            if f.name and f.name not in seen:
                seen.add(f.name)
                unique.append(f)
        self.fields = unique
        return self

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# RELATION
# ─────────────────────────────────────────────────────────────────────────────

class RelationModel(BaseModel):
    from_obj: str = Field(default="", alias="from")
    to_obj: str = Field(default="", alias="to")
    type: str = Field(default="ONE_TO_MANY")
    label: str = Field(default="")

    @field_validator("from_obj", "to_obj", mode="before")
    @classmethod
    def clean_endpoints(cls, v: Any) -> str:
        return _safe_str(v)

    @field_validator("type", mode="before")
    @classmethod
    def clean_type(cls, v: Any) -> str:
        raw = _safe_str(v, "ONE_TO_MANY").upper().strip()
        return raw if raw in VALID_RELATION_TYPES else "ONE_TO_MANY"

    @field_validator("label", mode="before")
    @classmethod
    def clean_label(cls, v: Any) -> str:
        return _safe_str(v)

    def to_dict(self) -> dict:
        """Serializa com as chaves originais ('from'/'to') para retrocompatibilidade."""
        return {
            "from": self.from_obj,
            "to": self.to_obj,
            "type": self.type,
            "label": self.label,
        }

    model_config = {"populate_by_name": True, "populate_by_alias": True}


# ─────────────────────────────────────────────────────────────────────────────
# ACTION
# ─────────────────────────────────────────────────────────────────────────────

class ActionModel(BaseModel):
    name: str = Field(default="")
    type: str = Field(default="DOMAIN_ACTION")
    description: str = Field(default="")
    trigger: str = Field(default="manual")
    entities_involved: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    postconditions: List[str] = Field(default_factory=list)

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_str(cls, v: Any) -> str:
        return _safe_str(v)

    @field_validator("type", mode="before")
    @classmethod
    def clean_type(cls, v: Any) -> str:
        raw = _safe_str(v, "DOMAIN_ACTION").upper().strip()
        return raw if raw in VALID_ACTION_TYPES else "DOMAIN_ACTION"

    @field_validator("trigger", mode="before")
    @classmethod
    def clean_trigger(cls, v: Any) -> str:
        raw = _safe_str(v, "manual").lower().strip()
        return raw if raw in VALID_TRIGGERS else "manual"

    @field_validator(
        "entities_involved", "steps", "preconditions", "postconditions",
        mode="before",
    )
    @classmethod
    def coerce_str_list(cls, v: Any) -> List[str]:
        raw = _coerce_list(v)
        return [str(item).strip() for item in raw if item is not None and str(item).strip()]

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# WORKSPACE
# ─────────────────────────────────────────────────────────────────────────────

class WorkspaceModel(BaseModel):
    name: str = Field(default="")
    description: str = Field(default="")
    icon: str = Field(default="grid")
    color: str = Field(default="#6B7280")
    objects: List[str] = Field(default_factory=list)
    primary_entity: str = Field(default="")
    permissions: List[str] = Field(
        default_factory=lambda: ["VER", "CRIAR", "EDITAR", "APAGAR"]
    )

    @field_validator("name", "description", "icon", "color", mode="before")
    @classmethod
    def clean_str(cls, v: Any) -> str:
        return _safe_str(v)

    @field_validator("objects", "permissions", mode="before")
    @classmethod
    def coerce_str_list(cls, v: Any) -> List[str]:
        raw = _coerce_list(v)
        return [str(item).strip() for item in raw if item is not None and str(item).strip()]

    @field_validator("primary_entity", mode="before")
    @classmethod
    def clean_primary(cls, v: Any) -> str:
        return _safe_str(v)

    @model_validator(mode="after")
    def infer_primary_entity(self) -> "WorkspaceModel":
        if not self.primary_entity and self.objects:
            self.primary_entity = self.objects[0]
        return self

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# METADATA
# ─────────────────────────────────────────────────────────────────────────────

class MetadataModel(BaseModel):
    total_objects: int = Field(default=0)
    total_relations: int = Field(default=0)
    total_actions: int = Field(default=0)
    total_workspaces: int = Field(default=0)

    @field_validator(
        "total_objects", "total_relations", "total_actions", "total_workspaces",
        mode="before",
    )
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0


# ─────────────────────────────────────────────────────────────────────────────
# BLUEPRINT — raiz do schema, Single Source of Truth
# ─────────────────────────────────────────────────────────────────────────────

class BlueprintModel(BaseModel):
    objects: List[ObjectModel] = Field(default_factory=list)
    relations: List[RelationModel] = Field(default_factory=list)
    actions: List[ActionModel] = Field(default_factory=list)
    workspaces: List[WorkspaceModel] = Field(default_factory=list)
    metadata: MetadataModel = Field(default_factory=MetadataModel)

    @field_validator("objects", mode="before")
    @classmethod
    def coerce_objects(cls, v: Any) -> list:
        raw = _coerce_list(v)
        result = []
        for item in raw:
            # instâncias já validadas vindas do aggregator — serializar para dict
            if isinstance(item, ObjectModel):
                result.append(item.model_dump())
            elif isinstance(item, dict) and item.get("name"):
                result.append(item)
            # None e outros tipos são silenciosamente descartados
        return result

    @field_validator("relations", mode="before")
    @classmethod
    def coerce_relations(cls, v: Any) -> list:
        raw = _coerce_list(v)
        result = []
        for item in raw:
            if isinstance(item, RelationModel):
                result.append(item.to_dict())
            elif (
                isinstance(item, dict)
                and item.get("from") and item.get("to")
                and item["from"] != item["to"]
            ):
                result.append(item)
        return result

    @field_validator("actions", mode="before")
    @classmethod
    def coerce_actions(cls, v: Any) -> list:
        raw = _coerce_list(v)
        result = []
        for item in raw:
            if isinstance(item, ActionModel):
                result.append(item.model_dump())
            elif isinstance(item, dict) and item.get("name"):
                result.append(item)
        return result

    @field_validator("workspaces", mode="before")
    @classmethod
    def coerce_workspaces(cls, v: Any) -> list:
        raw = _coerce_list(v)
        result = []
        for item in raw:
            if isinstance(item, WorkspaceModel):
                result.append(item.model_dump())
            elif isinstance(item, dict) and item.get("name"):
                result.append(item)
        return result

    @model_validator(mode="after")
    def sync_metadata(self) -> "BlueprintModel":
        self.metadata = MetadataModel(
            total_objects=len(self.objects),
            total_relations=len(self.relations),
            total_actions=len(self.actions),
            total_workspaces=len(self.workspaces),
        )
        return self

    # ── Serialização retrocompatível ──────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Exporta para dict puro com as chaves originais do projecto.
        Usar sempre que precisar de passar o blueprint para código legado
        (cache, storage, frontend, validator.py, canonical_schema.py, etc.).
        """
        return {
            "objects": [
                {
                    "name": obj.name,
                    "fields": [{"name": f.name, "type": f.type} for f in obj.fields],
                }
                for obj in self.objects
            ],
            "relations": [rel.to_dict() for rel in self.relations],
            "actions": [
                {
                    "name": act.name,
                    "type": act.type,
                    "description": act.description,
                    "trigger": act.trigger,
                    "entities_involved": act.entities_involved,
                    "steps": act.steps,
                    "preconditions": act.preconditions,
                    "postconditions": act.postconditions,
                }
                for act in self.actions
            ],
            "workspaces": [
                {
                    "name": ws.name,
                    "description": ws.description,
                    "icon": ws.icon,
                    "color": ws.color,
                    "objects": ws.objects,
                    "primary_entity": ws.primary_entity,
                    "permissions": ws.permissions,
                }
                for ws in self.workspaces
            ],
            "metadata": {
                "total_objects": self.metadata.total_objects,
                "total_relations": self.metadata.total_relations,
                "total_actions": self.metadata.total_actions,
                "total_workspaces": self.metadata.total_workspaces,
            },
        }

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÃO DE ENTRADA PÚBLICA
# ─────────────────────────────────────────────────────────────────────────────

def parse_blueprint(raw: Any) -> BlueprintModel:
    """
    Ponto de entrada único para converter qualquer valor (dict, None, lixo do LLM)
    num BlueprintModel validado e seguro.

    Nunca levanta excepção: em caso de falha total, devolve um BlueprintModel vazio.

    Uso:
        bp = parse_blueprint(some_dirty_dict)
        safe_dict = bp.to_dict()   # retrocompatível com o pipeline legado
    """
    if not isinstance(raw, dict):
        return BlueprintModel()
    try:
        return BlueprintModel.model_validate(raw)
    except Exception as exc:
        print(f"[models] parse_blueprint WARN: {exc}")
        return BlueprintModel()
