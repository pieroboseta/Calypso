# Calypso

Calypso is a private, source-grounded offline AI desktop application for Windows.

It lets users add PDF, DOCX, and TXT files, build a local searchable library, and ask questions using a locally running AI model. Calypso also includes a normal Chat mode for questions that do not require the document library.

## Main features

- Private local AI workflow
- PDF, DOCX, and TXT document support
- Source-grounded Research mode
- Normal Chat mode
- Source previews and page references
- Incremental document indexing
- Document collections
- Local chat history
- Library and storage management
- Light and dark themes

## Requirements

- Windows 10 or Windows 11
- Ollama
- The `qwen2.5:3b` model

After installing Ollama, run:

```powershell
ollama pull qwen2.5:3b
```

## Installation

Download the newest Windows installer from the Releases section of this repository.

## Running from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python .\src\main.py
```

## Privacy

Calypso processes the user's document library locally. Ollama and the selected model run on the user's computer.

## License

A license has not yet been selected.
