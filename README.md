# bbclaw — Sistema de Agentes Auto-Mejorable

Un sistema de agentes de IA minimalista, local-first y auto-mejorable.

## Estructura

```
bbclaw/
├── bbclaw/
│   ├── core/          # Orquestador, clase base Agent
│   ├── memory/        # SQLite + sqlite-vec (memoria vectorial)
│   ├── providers/     # Codex OAuth, OpenAI API (extensible)
│   ├── tools/         # filesystem, terminal, registry
│   └── main.py        # CLI entry point
├── config/
│   └── default.toml   # Configuración del sistema
├── workspace/         # Directorio de trabajo del agente
└── data/
    └── memory.db      # SQLite (conversaciones + vectores)
```

## Instalación

```bash
# Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# Instalar dependencias
pip install -e ".[dev]"
```

## Uso

```bash
# Iniciar REPL interactivo (la primera vez abre el browser para auth OAuth)
bbclaw

# Con configuración alternativa
bbclaw --config config/mi_config.toml

# Con logs detallados
bbclaw --verbose
```

## Comandos del REPL

| Comando | Descripción |
|---------|-------------|
| `/help` | Muestra ayuda |
| `/tools` | Lista herramientas disponibles |
| `/history` | Últimas conversaciones |
| `/logout` | Elimina token OAuth guardado |
| `/exit` | Salir |

## Autenticación

Por defecto usa **OAuth 2.0 con PKCE** para autenticarse con OpenAI Codex (`gpt-5.3-codex`). La primera vez abre el browser. El token se guarda en el keyring del sistema operativo y se refresca automáticamente.

Para usar API Key convencional, editar `config/default.toml`:

```toml
[provider]
default = "openai_api"
```

Y setear la variable de entorno `OPENAI_API_KEY`.

## Auto-mejora

El agente tiene acceso a leer y modificar su propio código. Podés pedirle:

> "Agregá una nueva herramienta que haga búsqueda web"
> "Optimizá el system prompt del agente"
> "Creá un nuevo agente especialista en Python"

## Fases

- **Fase 1** ✅ Core mínimo (agente único + memoria + herramientas)
- **Fase 2** 🔜 Multi-agente (planner + paralelismo + agentes especializados)
- **Fase 3** 🔜 Auto-mejora avanzada (tests + git integration)
- **Fase 4** 🔜 Extensibilidad (multi-provider, skills, API HTTP)
