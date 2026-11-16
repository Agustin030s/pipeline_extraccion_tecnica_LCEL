"""Contrato de datos del pipeline.

Este módulo define QUÉ debe devolver el LLM. Todo lo que salga de la cadena
tiene que pasar por estos modelos Pydantic: si no cumple, se considera inválido.
"""

import logging
from enum import Enum

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class NivelCriticidad(str, Enum):
    """Niveles de criticidad permitidos.

    Heredar de `str` hace que el valor se serialice como texto plano en el JSON
    ("alta" y no "NivelCriticidad.ALTA"), y que el modelo solo pueda elegir
    entre estas tres opciones (en el JSON Schema aparece como `enum`).
    """

    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class ExtraccionTecnica(BaseModel):
    """Información técnica estructurada extraída de un texto libre."""

    # IMPORTANTE: las `description` no son solo documentación para humanos.
    # `with_structured_output` convierte este modelo en un JSON Schema y se lo
    # envía al LLM, así que cada `description` funciona como una mini-instrucción
    # del prompt. Cuanto más precisas sean, mejor rellena el modelo cada campo.

    tecnologias: list[str] = Field(
        min_length=1,
        description=(
            "Lista de tecnologías, lenguajes, frameworks, bases de datos o "
            "servicios mencionados explícitamente en el texto. Sin duplicados. "
            "Debe contener al menos un elemento."
        ),
    )
    nivel_de_criticidad: NivelCriticidad = Field(
        description=(
            "Gravedad del problema o situación descrita. "
            "'baja': mejora, duda o detalle sin impacto en usuarios. "
            "'media': degradación de rendimiento o fallo parcial con workaround. "
            "'alta': caída en producción, pérdida de datos, riesgo de seguridad "
            "o un cuello de botella que afecta a muchos usuarios."
        ),
    )
    resumen_tecnico: str = Field(
        min_length=10,
        max_length=300,
        description=(
            "Resumen técnico en español, de una o dos frases, que explique qué "
            "ocurre y qué componentes están involucrados."
        ),
    )

    @field_validator("tecnologias")
    @classmethod
    def limpiar_tecnologias(cls, valores: list[str]) -> list[str]:
        """Normaliza la lista: quita espacios, vacíos y duplicados.

        `min_length=1` se comprueba ANTES de este validador, por eso volvemos a
        verificar al final: una lista como ["  ", ""] pasa la primera regla pero
        queda vacía después de limpiarla.
        """
        limpias: list[str] = []
        vistas: set[str] = set()
        for valor in valores:
            nombre = valor.strip()
            # Comparamos en minúsculas para tratar "Redis" y "redis" como la
            # misma tecnología, pero conservamos la primera forma que apareció.
            clave = nombre.lower()
            if nombre and clave not in vistas:
                vistas.add(clave)
                limpias.append(nombre)

        if not limpias:
            raise ValueError("La lista de tecnologías no puede quedar vacía.")

        if len(limpias) != len(valores):
            logger.info("Tecnologías normalizadas: %s -> %s", valores, limpias)
        return limpias
