# ===== handlers/chat_handler.py =====

import requests
import datetime
from config import MODELS, OPTIONS, OLLAMA_URL

def _get_pt_date() -> str:
    now = datetime.datetime.now()
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return f"{dias[now.weekday()]}, {now.day} de {meses[now.month - 1]} de {now.year}"

def handle_chat(prompt: str):
    
    data_atual = _get_pt_date()
    
    system_prompt = f"""
    Tu és o AiBizCore, um assistente virtual inteligente, direto e natural, especializado em Arquitetura de Software.
    
    CONTEXTO: A data de hoje é {data_atual}.
    
    REGRAS CRÍTICAS:
    1. Responde SEMPRE em português de Portugal (PT-PT).
    2. Sê inteligente e fluído. PROIBIDO repetir frases feitas como "Como posso ajudar-te hoje?" em todas as mensagens.
    3. Se o utilizador perguntar a data ou o dia, responde de forma completa usando a informação do CONTEXTO.
    4. Se o utilizador fizer uma afirmação (ex: "hoje é dia X"), concorda ou corrige com naturalidade.
    """

    payload = {
        "model": MODELS["router"],
        "prompt": f"{system_prompt}\n\nUtilizador: {prompt}\nAiBizCore:",
        "stream": False,
        "options": OPTIONS
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=30)
        res.raise_for_status()
        resposta = res.json().get("response", "").strip()
        return f" AiBizCore: {resposta}"
    except Exception as e:
        return f" AiBizCore: Erro na conversa: {e}"
