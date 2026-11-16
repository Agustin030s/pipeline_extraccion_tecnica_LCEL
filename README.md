# pipeline-extraccion-tecnica

Pipeline asíncrono con LangChain que recibe texto técnico libre (descripciones de arquitectura, logs de error, tickets) y devuelve un objeto estructurado y validado con:

- `tecnologias`: tecnologías mencionadas explícitamente.
- `nivel_de_criticidad`: `baja`, `media` o `alta`.
- `resumen_tecnico`: resumen en español de una o dos frases.

```
texto → ChatPromptTemplate → LLM.with_structured_output → validar_salida (+ with_retry) → ExtraccionTecnica
```

## Estructura

```
pipeline-extraccion-tecnica/
├── schemas.py        # contrato de datos (Pydantic)
├── chain.py          # modelo, prompt, cadena LCEL, resiliencia y process_text()
├── test_pipeline.py  # mini-script de prueba
├── requirements.txt  # dependencias
├── .env.example
├── .gitignore
└── README.md
```

## Instalación

Requiere Python 3.12.

**Windows (PowerShell)**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

**macOS / Linux**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Luego completa tu API key en `.env`.

## Variables de entorno

| Variable            | Descripción                                   | Valor por defecto    |
|---------------------|-----------------------------------------------|----------------------|
| `LLM_PROVIDER`      | Proveedor: `openai` o `anthropic`             | `openai`             |
| `OPENAI_API_KEY`    | API key de OpenAI                             | —                    |
| `ANTHROPIC_API_KEY` | API key de Anthropic                          | —                    |
| `OPENAI_MODEL`      | Modelo de OpenAI                              | `gpt-4o-mini`        |
| `ANTHROPIC_MODEL`   | Modelo de Anthropic                           | `claude-sonnet-5-5`  |
| `LLM_MAX_TOKENS`    | Máximo de tokens de la respuesta              | `1024`               |

## Ejecución

```bash
python test_pipeline.py
```

Primero corre las pruebas del esquema (sin API, no gastan créditos). Si hay una API key configurada, sigue con cuatro casos: arquitectura, log de error, texto ambiguo y un modelo con `max_tokens=20` para forzar una respuesta truncada.

### Ejemplo de salida

```json
{
  "tecnologias": ["FastAPI", "Redis", "PostgreSQL"],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "API con caché en Redis y persistencia en PostgreSQL; cuello de botella en conexiones concurrentes."
}
```

### Uso desde otro archivo

```python
import asyncio
from dotenv import load_dotenv
from chain import process_text

async def main():
    resultado = await process_text("Migramos de MySQL a PostgreSQL y Celery dejó de procesar tareas.")
    if resultado:  # process_text devuelve None si algo falla
        print(resultado.tecnologias, resultado.nivel_de_criticidad.value)

load_dotenv()
asyncio.run(main())
```

## Resultados de la ejecución (proveedor Anthropic)

Salida real de `python test_pipeline.py` con `LLM_PROVIDER=anthropic`:

| Caso | Resultado | Qué demuestra |
|---|---|---|
| Esquema (sin API) | 1 válido, 3 rechazados | Pydantic limpia duplicados y espacios, y rechaza lista vacía, criticidad inexistente y resumen corto |
| 1. Arquitectura | `alta`, `[FastAPI, Redis, PostgreSQL]` | Extracción correcta en el primer intento (`end_turn`) |
| 2. Log de error | `alta`, `[Python, psycopg2, PostgreSQL, Kubernetes]` | Interpreta un traceback real y detecta la caída en producción |
| 3. Texto ambiguo | `media`, `[base de datos, red]` | El modelo se recupera, aunque lista términos genéricos en vez de tecnologías concretas |
| 4. `max_tokens=20` | `None` tras 3 intentos | Se detecta `stop_reason="max_tokens"`, se lanza `RespuestaIncompletaError`, se reintenta y el programa no se rompe |

<details>
<summary>Log completo</summary>

```
======================================================================
PRUEBAS DE ESQUEMA (sin API)
======================================================================
15:10:36 | INFO     | schemas | Tecnologías normalizadas: [' FastAPI ', 'Redis', 'redis', '', 'PostgreSQL'] -> ['FastAPI', 'Redis', 'PostgreSQL']
[OK] Caso válido (con espacios, vacíos y duplicados que se limpian):
{
  "tecnologias": [
    "FastAPI",
    "Redis",
    "PostgreSQL"
  ],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "API con caché en Redis; cuello de botella en PostgreSQL."
}
[RECHAZADO] Lista de tecnologías vacía -> tecnologias: List should have at least 1 item after validation, not 0
[RECHAZADO] Criticidad 'urgente' (no existe en el Enum) -> nivel_de_criticidad: Input should be 'baja', 'media' or 'alta'
[RECHAZADO] Resumen demasiado corto -> resumen_tecnico: String should have at least 10 characters

======================================================================
1. Descripción de arquitectura
======================================================================
15:10:42 | INFO     | chain | Procesando texto (285 caracteres)...
15:10:42 | INFO     | chain | Creando modelo: proveedor=anthropic, max_tokens=1024
15:10:46 | INFO     | httpx2 | HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
15:10:46 | INFO     | chain | Intento de validación #1...
15:10:46 | INFO     | chain | Motivo de corte del modelo: end_turn
15:10:46 | INFO     | chain | Validación OK
15:10:46 | INFO     | chain | Extracción completada: {'tecnologias': ['FastAPI', 'Redis', 'PostgreSQL'], 'nivel_de_criticidad': <NivelCriticidad.ALTA: 'alta'>, 'resumen_tecnico': 'En horas pico el pool de conexiones a PostgreSQL de la API FastAPI se satura, y las peticiones tardan másde 5 segundos. Esto provoca timeouts en el frontend para miles de usuarios, mientras Redis solo actúa como caché de sesiones.'}
{
  "tecnologias": [
    "FastAPI",
    "Redis",
    "PostgreSQL"
  ],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "En horas pico el pool de conexiones a PostgreSQL de la API FastAPI se satura, y las peticiones tardan más de 5 segundos.Esto provoca timeouts en el frontend para miles de usuarios, mientras Redis solo actúa como caché de sesiones."
}

======================================================================
2. Log de error en producción
======================================================================
15:10:46 | INFO     | chain | Procesando texto (679 caracteres)...
15:10:49 | INFO     | httpx2 | HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
15:10:49 | INFO     | chain | Intento de validación #1...
15:10:49 | INFO     | chain | Motivo de corte del modelo: end_turn
15:10:49 | INFO     | chain | Validación OK
15:10:49 | INFO     | chain | Extracción completada: {'tecnologias': ['Python', 'psycopg2', 'PostgreSQL', 'Kubernetes'], 'nivel_de_criticidad': <NivelCriticidad.ALTA: 'alta'>, 'resumen_tecnico': 'El servicio api-pagos no logra conectarse a la base de datos db-prod.internal (puerto 5432) por un error de conexión rechazada en psycopg2. El health check falla y Kubernetes reinicia el pod, lo que indica una caída en producción.'}
{
  "tecnologias": [
    "Python",
    "psycopg2",
    "PostgreSQL",
    "Kubernetes"
  ],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "El servicio api-pagos no logra conectarse a la base de datos db-prod.internal (puerto 5432) por un error de conexión rechazada en psycopg2. El health check falla y Kubernetes reinicia el pod, lo que indica una caída en producción."
}

======================================================================
3. Estrés: texto ambiguo sin tecnologías claras
======================================================================
15:10:49 | INFO     | chain | Procesando texto (64 caracteres)...
15:10:51 | INFO     | httpx2 | HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
15:10:51 | INFO     | chain | Intento de validación #1...
15:10:51 | INFO     | chain | Motivo de corte del modelo: end_turn
15:10:51 | INFO     | chain | Validación OK
15:10:51 | INFO     | chain | Extracción completada: {'tecnologias': ['base de datos', 'red'], 'nivel_de_criticidad': <NivelCriticidad.MEDIA: 'media'>, 'resumen_tecnico': 'El sistema sufre lentitud intermitente, una degradación de rendimiento cuya causa sin confirmar podría estar en la base de datos o en la red.'}
{
  "tecnologias": [
    "base de datos",
    "red"
  ],
  "nivel_de_criticidad": "media",
  "resumen_tecnico": "El sistema sufre lentitud intermitente, una degradación de rendimiento cuya causa sin confirmar podría estar en la base de datos o en la red."
}

======================================================================
4. finish_reason: modelo con max_tokens=20
======================================================================
15:10:51 | INFO     | chain | Creando modelo: proveedor=anthropic, max_tokens=20
15:10:51 | INFO     | chain | Procesando texto (285 caracteres)...
15:10:53 | INFO     | httpx2 | HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
15:10:53 | INFO     | chain | Intento de validación #1...
15:10:53 | INFO     | chain | Motivo de corte del modelo: max_tokens
15:10:53 | WARNING  | chain | Respuesta truncada por límite de tokens (max_tokens).
15:10:56 | INFO     | httpx2 | HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
15:10:56 | INFO     | chain | Intento de validación #2...
15:10:56 | INFO     | chain | Motivo de corte del modelo: max_tokens
15:10:56 | WARNING  | chain | Respuesta truncada por límite de tokens (max_tokens).
15:10:59 | INFO     | httpx2 | HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
15:10:59 | INFO     | chain | Intento de validación #3...
15:10:59 | INFO     | chain | Motivo de corte del modelo: max_tokens
15:10:59 | WARNING  | chain | Respuesta truncada por límite de tokens (max_tokens).
15:10:59 | ERROR    | chain | Se agotaron los reintentos [RespuestaIncompletaError]: El modelo se quedó sin tokens (motivo: max_tokens).
-> Resultado: None (el pipeline falló de forma controlada)
```

</details>

## Decisiones de diseño

- **`with_structured_output`**: convierte el modelo Pydantic en un JSON Schema y usa la salida estructurada nativa del proveedor (`method="json_schema"`, porque los modelos actuales de Anthropic no admiten el método por defecto, *forced tool calling*). Las `description` de cada campo llegan al LLM, así que también son parte del prompt.
- **`include_raw=True` y `finish_reason`**: sin esta opción solo recibimos el objeto ya interpretado y perdemos el mensaje original. Con ella, `validar_salida` lee `finish_reason` (OpenAI) o `stop_reason` (Anthropic). Si el valor es `length` o `max_tokens`, la respuesta está cortada y se lanza `RespuestaIncompletaError` antes de intentar usarla.
- **Qué se reintenta** (`with_retry`, hasta 3 intentos): respuestas truncadas, salidas que no cumplen el esquema, errores de conexión, rate limit y timeouts. El retry envuelve al modelo **y** a `validar_salida`. Si solo envolviera al modelo, un JSON inválido nunca provocaría un nuevo intento.
- **Qué NO se reintenta**: los errores de autenticación (API key inválida) y de configuración, porque no se arreglan repitiendo la llamada. `process_text` los registra y devuelve `None`.
- **Prompt con variables y no f-strings**: `ChatPromptTemplate` con `{instrucciones_formato}` y `{texto}`. Las instrucciones se fijan con `prompt.partial(...)`, así el template se puede reutilizar con otras instrucciones y el texto del usuario nunca se concatena a mano.

> **Nota:** los modelos actuales de Anthropic no aceptan el parámetro `temperature` (la API responde con un error 400). Por eso `ChatAnthropic` se crea sin él.
