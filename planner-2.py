# ===== planner.py =====

import json
import re
import requests

from config import MODELS, OPTIONS, OLLAMA_URL


SYSTEM_PROMPT = """
You are a system architect.

Convert a user request into a structured system plan.

CRITICAL RULE: All generated text, entity names, descriptions, fields, and workspaces MUST be written strictly in European Portuguese (pt-PT). Keep structural JSON keys in English.

OUTPUT ONLY VALID JSON.

Format:

{
  "domain": "nome_do_dominio",
  "entities": [
    {
      "name": "NomeDaEntidade",
      "purpose": "Descrição detalhada do propósito em português",
      "suggested_fields": ["campo1", "campo2"]
    }
  ],
  "relations": [
    {
      "from": "EntidadeA",
      "to": "EntidadeB",
      "type": "ONE_TO_MANY | MANY_TO_MANY"
    }
  ],
  "workspaces": [
    {
      "name": "NomeDoPerfil",
      "entities": ["Entidade1", "Entidade2"]
    }
  ]
}

Rules:
- Minimum 5 entities
- Keep it concise
- No explanations
"""


DOMAIN_CONTEXT = {
    "hospital": ["Paciente", "Medico", "Consulta"],
    "logistics": ["Encomenda", "Armazem", "Entrega"],
    "ecommerce": ["Cliente", "Produto", "Pagamento"],
    "manufacturing": ["Produto", "Maquina", "OrdemProducao"],
    "finance": ["Conta", "Transacao", "Pagamento"],
    "education": ["Aluno", "Professor", "Disciplina"],
    "city": ["Cidadao", "Servico", "Infraestrutura"]
}


def _extract_json(text: str):
    try:
        return json.loads(text)
    except:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                return None
    return None


def _detect_domain(prompt: str) -> str:
    p = prompt.lower()

    domain_keywords = {
        "hospital": ["hospital", "medico", "paciente"],
        "logistics": ["logistica", "armazem", "entrega"],
        "ecommerce": ["loja", "produto", "compra"],
        "manufacturing": ["fabrica", "producao"],
        "finance": ["banco", "finance"],
        "education": ["escola", "aluno"],
        "city": ["cidade", "municipio"]
    }

    for domain, keywords in domain_keywords.items():
        if any(k in p for k in keywords):
            return domain

    return "generic"


def _build_context_hint(domain: str):
    entities = DOMAIN_CONTEXT.get(domain, [])
    if not entities:
        return ""

    return f"\nExamples: {', '.join(entities)}"


def generate_plan(prompt: str, feedback: dict = None) -> dict:

    domain = _detect_domain(prompt)

    context_hint = _build_context_hint(domain)

    full_prompt = f"{SYSTEM_PROMPT}\n{context_hint}\n\nRequest:\n{prompt}"

    payload = {
        "model": MODELS["planner"],
        "prompt": full_prompt,
        "stream": False,
        "options": {
            **OPTIONS,
            "temperature": 0.3,
            "num_predict": 600  # 🔥 reduzido (ANTES: 1500)
        }
    }

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=120  # 🔥 aumentado
        )

        response.raise_for_status()

        raw = response.json().get("response", "").strip()

        plan = _extract_json(raw)

        if not plan:
            print("[planner] fallback usado")

            # 🔥 fallback melhor (não vazio)
            return {
                "domain": domain,
                "entities": [
                    {"name": "EntidadeBase", "purpose": "Entidade de segurança em caso de falha"}
                ],
                "relations": [],
                "workspaces": [
                    {"name": "Geral", "entities": ["EntidadeBase"]}
                ]
            }

        # garantir estrutura mínima
        plan.setdefault("domain", domain)
        plan.setdefault("entities", [])
        plan.setdefault("relations", [])
        plan.setdefault("workspaces", [])

        print(
            f"[planner] OK — {len(plan['entities'])} entidades — domínio: {plan['domain']}"
        )

        return plan

    except Exception as e:
        print(f"[planner] erro: {e}")
        return None
