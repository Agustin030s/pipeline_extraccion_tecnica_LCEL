"""Mini-script de prueba del pipeline.

Uso:  python test_pipeline.py

1. Pruebas de esquema (sin API, no gastan créditos).
2. Pruebas con el modelo (solo si hay API key configurada).
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from pydantic import ValidationError

from schemas import ExtraccionTecnica

TEXTO_ARQUITECTURA = """\
Nuestra API está construida con FastAPI y usa Redis como caché de sesiones.
Los datos persistentes viven en PostgreSQL. En horas pico, el pool de
conexiones a PostgreSQL se satura y las peticiones tardan más de 5 segundos,
lo que provoca timeouts en el frontend para miles de usuarios."""

TEXTO_LOG = """\
2026-11-16 03:12:44 ERROR [api-pagos] Unhandled exception in worker
Traceback (most recent call last):
  File "/app/db/session.py", line 42, in get_connection
    conn = psycopg2.connect(dsn=settings.DATABASE_URL, connect_timeout=5)
  File "/usr/local/lib/python3.12/site-packages/psycopg2/__init__.py", line 122, in connect
    conn = _connect(dsn, connection_factory=connection_factory, **kwasync)
psycopg2.OperationalError: connection to server at "db-prod.internal" (10.0.3.17), port 5432 failed: Connection refused
	Is the server running on that host and accepting TCP/IP connections?
2026-11-16 03:12:44 CRITICAL [api-pagos] Health check failed, pod restarting (Kubernetes)"""

TEXTO_AMBIGUO = "El sistema anda lento a veces, creo que es por la base o la red."


def separador(titulo: str) -> None:
    print(f"\n{'=' * 70}\n{titulo}\n{'=' * 70}")


def mostrar(resultado: ExtraccionTecnica | None) -> None:
    if resultado is None:
        print("-> Resultado: None (el pipeline falló de forma controlada)")
    else:
        print(resultado.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# 1. Pruebas del esquema (sin API)
# ---------------------------------------------------------------------------
def probar_esquema() -> None:
    separador("PRUEBAS DE ESQUEMA (sin API)")

    valido = ExtraccionTecnica(
        tecnologias=[" FastAPI ", "Redis", "redis", "", "PostgreSQL"],
        nivel_de_criticidad="alta",
        resumen_tecnico="API con caché en Redis; cuello de botella en PostgreSQL.",
    )
    print("[OK] Caso válido (con espacios, vacíos y duplicados que se limpian):")
    print(valido.model_dump_json(indent=2))

    casos_invalidos = {
        "Lista de tecnologías vacía": {
            "tecnologias": [],
            "nivel_de_criticidad": "media",
            "resumen_tecnico": "Resumen suficientemente largo.",
        },
        "Criticidad 'urgente' (no existe en el Enum)": {
            "tecnologias": ["Docker"],
            "nivel_de_criticidad": "urgente",
            "resumen_tecnico": "Resumen suficientemente largo.",
        },
        "Resumen demasiado corto": {
            "tecnologias": ["Docker"],
            "nivel_de_criticidad": "baja",
            "resumen_tecnico": "Corto",
        },
    }

    for nombre, datos in casos_invalidos.items():
        try:
            ExtraccionTecnica(**datos)
            print(f"[??] {nombre}: se aceptó (no debería)")
        except ValidationError as exc:
            # Mostramos solo campo + mensaje, sin el bloque completo de Pydantic.
            errores = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
            )
            print(f"[RECHAZADO] {nombre} -> {errores}")


# ---------------------------------------------------------------------------
# 2. Pruebas con el modelo
# ---------------------------------------------------------------------------
async def probar_modelo() -> None:
    # Importamos aquí para que las pruebas de esquema funcionen aunque falten
    # dependencias del proveedor.
    from chain import build_chain, get_model, process_text

    casos = [
        ("1. Descripción de arquitectura", TEXTO_ARQUITECTURA),
        ("2. Log de error en producción", TEXTO_LOG),
        ("3. Estrés: texto ambiguo sin tecnologías claras", TEXTO_AMBIGUO),
    ]
    for titulo, texto in casos:
        separador(titulo)
        mostrar(await process_text(texto))

    separador("4. finish_reason: modelo con max_tokens=20")
    try:
        chain_corta = build_chain(get_model(max_tokens=20))
    except (ValueError, ImportError) as exc:
        logging.error("No se pudo crear el modelo corto [%s]: %s", type(exc).__name__, exc)
        return
    mostrar(await process_text(TEXTO_ARQUITECTURA, chain=chain_corta))


async def main() -> None:
    probar_esquema()

    proveedor = (os.getenv("LLM_PROVIDER") or "openai").lower()
    variable_key = f"{proveedor.upper()}_API_KEY"
    if not os.getenv(variable_key):
        separador("PRUEBAS CON EL MODELO")
        print(f"No hay {variable_key} configurada: se omiten las pruebas con el modelo.")
        print("Copia .env.example a .env y completa tu API key para ejecutarlas.")
        return

    await probar_modelo()


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(main())
