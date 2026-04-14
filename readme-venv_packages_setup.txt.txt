Sequence of steps:

##setup virtual environment and activate it.
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate

#install required setuptools and wheel
pip install --upgrade pip setuptools wheel

#install required packages from 'pyproject.toml'
pip install -e ".[dev]"


#setup .env file and necessary API keys
cp .env.example .env          # add your OPENAI_API_KEY


#setup vector DB.
cd rcm-denial-proto
python scripts/seed_knowledge_base.py   # seed ChromaDB (needs API key)

#to execute every time
cd rcm-denial-proto
rcm-denial process-batch data/sample_denials.csv




=========================================================
Here is the content of [pyproject.toml] - Start
------------------------------------------------
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "rcm-denial-management"
version = "1.0.0"
description = "Agentic AI based RCM Denial Management System"
authors = [{ name = "RK", email = "kvrkr866@gmail.com" }]
requires-python = ">=3.11"
dependencies = [
    "langgraph>=0.2.0",
    "langchain>=0.3.0",
    "langchain-openai>=0.2.0",
    "langchain-community>=0.3.0",
    "langchain-chroma>=0.1.0",
    "chromadb>=0.5.0",
    "openai>=1.40.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
    "python-dotenv>=1.0.0",
    "structlog>=24.1.0",
    "tenacity>=8.3.0",
    "pytesseract>=0.3.10",
    "pdf2image>=1.17.0",
    "pypdf>=4.2.0",
    "reportlab>=4.2.0",
    "fpdf2>=2.7.9",
    "Pillow>=10.3.0",
    "pandas>=2.2.0",
    "httpx>=0.27.0",
    "rich>=13.7.0",
    "click>=8.1.7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.4.0",
    "mypy>=1.10.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[project.scripts]
rcm-denial = "main:cli"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = false
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

--------------------------------------------
Here is the end of [pyproject.toml] - End