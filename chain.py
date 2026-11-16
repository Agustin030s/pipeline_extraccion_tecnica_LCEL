"""Pipeline de extracción técnica con LangChain (LCEL).

Flujo:
    texto -> ChatPromptTemplate -> LLM.with_structured_output
          -> validar_salida (+ with_retry) -> ExtraccionTecnica
"""

import logging
import os
from contextvars import ContextVar
from typing import Any, Callable

import anthropic
import openai
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from schemas import ExtraccionTecnica

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Excepciones propias
# ---------------------------------------------------------------------------
class RespuestaIncompletaError(Exception):
    """El modelo se quedó sin tokens y la respuesta llegó cortada."""


class SalidaInvalidaError(Exception):
    """La respuesta no es JSON válido o no cumple el esquema ExtraccionTecnica."""


# ---------------------------------------------------------------------------
# Modelo: Factory
# ---------------------------------------------------------------------------
def _crear_openai(max_tokens: int) -> BaseChatModel:
    # Import diferido: solo hace falta tener instalado el paquete del proveedor
    # que realmente se usa.
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        temperature=0,  # extracción = queremos respuestas deterministas
        max_tokens=max_tokens,
    )


def _crear_anthropic(max_tokens: int) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    # NO pasamos `temperature`: los modelos actuales de Anthropic la rechazan
    # y la API responde con un error 400. Usamos el valor por defecto.
    return ChatAnthropic(
        model=os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5-5",
        max_tokens=max_tokens,
    )


# Diccionario proveedor -> constructora. Agregar un proveedor nuevo es sumar
# una entrada aquí, sin tocar una cadena de if/elif.
_CONSTRUCTORES: dict[str, Callable[[int], BaseChatModel]] = {
    "openai": _crear_openai,
    "anthropic": _crear_anthropic,
}

_VARIABLES_API_KEY: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def get_model(provider: str | None = None, max_tokens: int | None = None) -> BaseChatModel:
    """Devuelve el chat model configurado en el .env (o el que se pida)."""
    proveedor = (provider or os.getenv("LLM_PROVIDER") or "openai").strip().lower()

    if proveedor not in _CONSTRUCTORES:
        raise ValueError(
            f"Proveedor '{proveedor}' no soportado. "
            f"Opciones válidas: {', '.join(_CONSTRUCTORES)}."
        )

    variable_key = _VARIABLES_API_KEY[proveedor]
    if not os.getenv(variable_key):
        raise ValueError(
            f"Falta la variable {variable_key} para usar el proveedor '{proveedor}'. "
            "Revisa tu archivo .env."
        )

    tokens = max_tokens or int(os.getenv("LLM_MAX_TOKENS", "1024"))
    logger.info("Creando modelo: proveedor=%s, max_tokens=%d", proveedor, tokens)
    return _CONSTRUCTORES[proveedor](tokens)


# ---------------------------------------------------------------------------
# Prompt modular
# ---------------------------------------------------------------------------
INSTRUCCIONES_FORMATO = """\
- Lista SOLO tecnologías mencionadas explícitamente en el texto; no inventes ni deduzcas otras.
- Elige la criticidad con estos criterios:
  * baja: mejoras, dudas o detalles sin impacto en usuarios.
  * media: degradación de rendimiento o fallos parciales con alternativa.
  * alta: caídas en producción, pérdida de datos, riesgos de seguridad o cuellos de botella graves.
- El resumen técnico va en español, en una o dos frases."""

# Usamos variables de plantilla ({...}) y NO f-strings: el template queda
# definido una sola vez y LangChain rellena los valores al ejecutar. Además,
# el texto del usuario nunca se concatena "a mano" dentro del prompt.
_prompt_base = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Eres un analista técnico senior. Extrae información estructurada "
            "del texto que te envíe el usuario.\n\n"
            "Instrucciones:\n{instrucciones_formato}",
        ),
        ("human", "Texto a analizar:\n\n{texto}"),
    ]
)

# `partial` fija las instrucciones de antemano y deja libre solo {texto}.
# El template base sigue siendo reutilizable con otras instrucciones.
prompt = _prompt_base.partial(instrucciones_formato=INSTRUCCIONES_FORMATO)


# ---------------------------------------------------------------------------
# Validación de la salida
# ---------------------------------------------------------------------------
# Motivos de corte que significan "se acabaron los tokens":
# OpenAI usa finish_reason="length"; Anthropic usa stop_reason="max_tokens".
_MOTIVOS_TRUNCADO = {"length", "max_tokens"}

# Contador de intentos por llamada a process_text. Guardamos una lista (objeto
# mutable) en un ContextVar para que cada ejecución tenga su propio contador,
# incluso si se procesan varios textos en paralelo con asyncio.
_intentos: ContextVar[list[int] | None] = ContextVar("intentos", default=None)


def validar_salida(resultado: dict[str, Any]) -> ExtraccionTecnica:
    """Revisa la respuesta cruda del modelo y devuelve el objeto validado.

    `resultado` viene de with_structured_output(include_raw=True):
    {"raw": AIMessage, "parsed": ExtraccionTecnica | None, "parsing_error": Exception | None}
    """
    contador = _intentos.get()
    if contador is not None:
        contador[0] += 1
        logger.info("Intento de validación #%d...", contador[0])
    else:
        logger.info("Intento de validación...")

    # 1. Primero miramos POR QUÉ terminó el modelo. Si se cortó por tokens,
    #    el JSON está incompleto y no tiene sentido intentar interpretarlo.
    metadata = resultado["raw"].response_metadata
    motivo = metadata.get("finish_reason") or metadata.get("stop_reason")
    logger.info("Motivo de corte del modelo: %s", motivo)

    if motivo in _MOTIVOS_TRUNCADO:
        logger.warning("Respuesta truncada por límite de tokens (%s).", motivo)
        raise RespuestaIncompletaError(
            f"El modelo se quedó sin tokens (motivo: {motivo})."
        )

    # 2. JSON mal formado o que no cumple el esquema Pydantic.
    if resultado["parsing_error"] is not None or resultado["parsed"] is None:
        logger.warning("Salida inválida: %s", resultado["parsing_error"])
        raise SalidaInvalidaError(
            f"La salida no cumple el esquema: {resultado['parsing_error']}"
        )

    # 3. Todo correcto.
    logger.info("Validación OK")
    return resultado["parsed"]


# ---------------------------------------------------------------------------
# Cadena LCEL con resiliencia
# ---------------------------------------------------------------------------
# Errores transitorios: vale la pena reintentar.
# AuthenticationError (401) NO está en la lista a propósito: una API key
# inválida no se arregla reintentando, solo gastaría tiempo y llamadas.
ERRORES_REINTENTABLES: tuple[type[BaseException], ...] = (
    RespuestaIncompletaError,
    SalidaInvalidaError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
)


def build_chain(model: BaseChatModel | None = None) -> Runnable:
    """Arma la cadena completa. Recibe un modelo para poder probar variantes
    (por ejemplo, uno con max_tokens muy bajo)."""
    model = model or get_model()

    # include_raw=True -> recibimos {"raw", "parsed", "parsing_error"} en vez
    # de solo el objeto. Lo necesitamos para leer finish_reason/stop_reason.
    # method="json_schema" usa la salida estructurada nativa de cada proveedor.
    # Los modelos actuales de Anthropic no admiten el método por defecto
    # ("forced tool calling"), y OpenAI también soporta json_schema.
    extractor = model.with_structured_output(
        ExtraccionTecnica, method="json_schema", include_raw=True
    ) | RunnableLambda(validar_salida)

    # .with_retry() envuelve al extractor COMPLETO (modelo + validar_salida).
    # Si solo envolviera al modelo, un JSON mal formado o una respuesta
    # truncada no lanzaría error dentro del bloque reintentado, y nunca se
    # volvería a pedir la respuesta.
    extractor_resiliente = extractor.with_retry(
        stop_after_attempt=3,
        retry_if_exception_type=ERRORES_REINTENTABLES,
    )

    # El prompt queda fuera del retry: formatear el template no falla de
    # forma transitoria, así que no hace falta repetirlo.
    return prompt | extractor_resiliente


_chain_por_defecto: Runnable | None = None


def _get_chain_por_defecto() -> Runnable:
    # Creamos la cadena la primera vez que se usa (y no al importar el módulo),
    # así importar chain.py no falla si todavía no hay API key configurada.
    global _chain_por_defecto
    if _chain_por_defecto is None:
        _chain_por_defecto = build_chain()
    return _chain_por_defecto


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
async def process_text(text: str, chain: Runnable | None = None) -> ExtraccionTecnica | None:
    """Extrae información técnica de `text`. Devuelve None si algo falla.

    Nunca lanza excepciones: los errores se registran en el log para que el
    programa que llama pueda seguir funcionando.
    """
    if not text or not text.strip():
        logger.warning("Texto vacío: no hay nada que procesar.")
        return None

    logger.info("Procesando texto (%d caracteres)...", len(text))
    _intentos.set([0])

    try:
        chain = chain or _get_chain_por_defecto()
        resultado = await chain.ainvoke({"texto": text})
    except (RespuestaIncompletaError, SalidaInvalidaError) as exc:
        logger.error(
            "Se agotaron los reintentos [%s]: %s", type(exc).__name__, exc
        )
        return None
    except (openai.AuthenticationError, anthropic.AuthenticationError) as exc:
        logger.error(
            "Error de autenticación [%s]: revisa tu API key (no se reintenta).",
            type(exc).__name__,
        )
        return None
    except (openai.APIError, anthropic.APIError) as exc:
        logger.error("Error de la API [%s]: %s", type(exc).__name__, exc)
        return None
    except ValueError as exc:
        logger.error("Error de configuración [%s]: %s", type(exc).__name__, exc)
        return None
    except ImportError as exc:
        logger.error(
            "Falta instalar el paquete del proveedor [%s]: %s", type(exc).__name__, exc
        )
        return None
    except Exception as exc:  # red de seguridad: el pipeline nunca rompe el programa
        logger.exception("Error inesperado [%s]: %s", type(exc).__name__, exc)
        return None

    logger.info("Extracción completada: %s", resultado.model_dump())
    return resultado
