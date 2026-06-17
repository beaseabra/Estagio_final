# ===== install_db_execution_router.py =====
# Script pequeno para integrar db_execution_routes.py no api.py atual.
# Não substitui o api.py. Só adiciona:
#   from db_execution_routes import router as db_execution_router
#   app.include_router(db_execution_router)

from pathlib import Path

API_PATH = Path("api.py")

if not API_PATH.exists():
    raise FileNotFoundError("api.py não encontrado. Corre este script na raiz do projeto AiBizCore_v4.")

s = API_PATH.read_text(encoding="utf-8")

import_line = "from db_execution_routes import router as db_execution_router\n"

if import_line not in s:
    if "from db_preview_routes import router as db_preview_router\n" in s:
        s = s.replace(
            "from db_preview_routes import router as db_preview_router\n",
            "from db_preview_routes import router as db_preview_router\n" + import_line,
            1,
        )
    elif "from main import run_pipeline\n" in s:
        s = s.replace(
            "from main import run_pipeline\n",
            "from main import run_pipeline\n" + import_line,
            1,
        )
    else:
        raise RuntimeError("Não encontrei um ponto seguro para inserir o import no api.py.")

include_line = "app.include_router(db_execution_router)\n"

if include_line not in s:
    if "app.include_router(db_preview_router)\n" in s:
        s = s.replace(
            "app.include_router(db_preview_router)\n",
            "app.include_router(db_preview_router)\n" + include_line,
            1,
        )
    elif "app.add_middleware(" in s:
        s = s.replace(
            "app.add_middleware(",
            "# Rotas de execução/dry-run SQL Server\n" + include_line + "\napp.add_middleware(",
            1,
        )
    else:
        raise RuntimeError("Não encontrei um ponto seguro para inserir app.include_router no api.py.")

API_PATH.write_text(s, encoding="utf-8")

print("api.py atualizado com db_execution_router.")
print("Confirma com:")
print("python3 -c \"from api import app; print([r.path for r in app.routes if 'db' in r.path])\"")
