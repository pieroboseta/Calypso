<div align="center">

<img src="assets/logo.png" alt="Calypso logo" width="150">

# Calypso

### Your private, source-grounded offline AI for Windows

[![Latest Release](https://img.shields.io/github/v/release/pieroboseta/Calypso?label=Latest%20Release)](https://github.com/pieroboseta/Calypso/releases/latest)
[![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-7A5C8E)](https://github.com/pieroboseta/Calypso/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.12-7A5C8E)](https://www.python.org/)

Calypso lets you build a private local library from your own documents, ask questions about them, and inspect the exact sources used for each answer.

[Download Calypso](https://github.com/pieroboseta/Calypso/releases/latest) · [Report a Problem](https://github.com/pieroboseta/Calypso/issues)

<br>

[![Donate with PayPal](https://img.shields.io/badge/Donate-PayPal-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/xsinerox)

</div>

---

## What Calypso does

Calypso is a Windows desktop AI application designed around privacy and local control.

You can add PDF, DOCX, and TXT files, build a searchable local library, and ask questions using **Research mode**. Calypso finds relevant passages, answers from your documents, and shows source references that you can open and preview.

For everyday questions that do not require documents, **Chat mode** lets you talk normally with the local AI.

Your documents, chats, summaries, indexes, and cache remain on your own computer.

## Features

- Private local AI powered by Ollama
- Research mode for PDF, DOCX, and TXT files
- Normal Chat mode for general questions
- Source previews and PDF page references
- Search the full library, one collection, or one document
- Incremental indexing for faster rebuilds
- Document summaries and suggested questions
- Local chat history
- Library, cache, and storage management
- Light and dark appearance modes
- Windows installer and uninstaller

---

# Beginner installation guide

Calypso needs **two separate programs**:

1. **Calypso** — the desktop interface
2. **Ollama** — the program that runs the AI model locally

You do **not** need Python to use the installed version.

## Step 1 — Download and install Calypso

1. Open the [latest Calypso release](https://github.com/pieroboseta/Calypso/releases/latest).
2. Expand the **Assets** section.
3. Download the file named:

```text
Calypso-Setup-1.0.0.exe
```

4. Open the downloaded installer.
5. Follow the installation steps.
6. Launch Calypso from the Start Menu or desktop shortcut.

> Windows may display an **Unknown publisher** message because the installer is not digitally signed yet. Confirm that the file was downloaded from this official repository before continuing.

## Step 2 — Install Ollama

Calypso cannot generate answers until Ollama is installed.

1. Open the [official Ollama download page](https://ollama.com/download/windows).
2. Download **Ollama for Windows**.
3. Open `OllamaSetup.exe`.
4. Complete the installation.
5. Ollama should start automatically in the background.

> Ollama is the program that runs the AI. It is not the same thing as Meta's Llama model.

## Step 3 — Download the Calypso AI model

1. Open the Windows Start Menu.
2. Search for **PowerShell**.
3. Open PowerShell.
4. Paste this command and press **Enter**:

```powershell
ollama pull qwen2.5:3b
```

5. Wait until the model finishes downloading.

The `qwen2.5:3b` model is approximately 1.9 GB, so the download time depends on your internet speed.

To confirm that it installed correctly, run:

```powershell
ollama list
```

You should see `qwen2.5:3b` in the list.

## Step 4 — Open Calypso

Launch Calypso after Ollama and the model are installed.

The first startup can take longer because Calypso loads the local AI and document-search components. Keep the loading screen open until the main window appears.

---

# Using Calypso

## Research mode

Use Research mode when you want answers based on your own documents.

1. Open Calypso.
2. Click **Manage**.
3. Click **Add Files**.
4. Select PDF, DOCX, or TXT files.
5. Close the Library Manager.
6. Click **Rebuild**.
7. Wait until the library is ready.
8. Select the entire library, a collection, or one document.
9. Ask your question.

Sources appear below the answer. Click a source to preview the exact passage Calypso used.

## Chat mode

Use Chat mode for general questions that do not need your files.

1. Select **Chat** at the top of the window.
2. Type a question.
3. Click **Send**.

Chat mode uses the local AI directly and does not search the document library or show document references.

---

## System requirements

- Windows 10 22H2 or newer, or Windows 11
- 64-bit computer
- 8 GB RAM recommended
- Several GB of free disk space
- Ollama
- `qwen2.5:3b`

A dedicated graphics card is not required, but faster hardware can improve response speed.

---

## Common problems

### Calypso says Ollama is unavailable

Make sure Ollama is running, then restart Calypso.

You can test Ollama from PowerShell:

```powershell
ollama list
```

### `qwen2.5:3b` is missing

Run:

```powershell
ollama pull qwen2.5:3b
```

### Research mode cannot find a document

Make sure that:

- the file is PDF, DOCX, or TXT
- the file appears in **Manage**
- you clicked **Rebuild** after adding it
- the correct document, collection, or library option is selected

### Research stops working after clearing the cache

Click **Rebuild**. Calypso must recreate the search index after the cache is cleared.

### Calypso opens slowly

The first launch and the first library rebuild can take longer. Later rebuilds are faster because unchanged document embeddings are cached.

---

## Privacy

Calypso is designed for local use:

- documents stay on your computer
- chats stay on your computer
- document indexes stay on your computer
- the AI model runs locally through Ollama

Calypso does not require uploading your document library to an online AI service.

---

## Uninstalling

Open:

```text
Windows Settings → Apps → Installed apps → Calypso → Uninstall
```

Back up any documents or chats you want to keep before uninstalling or using **Reset Calypso**.

---

## Support and feedback

Calypso is an early release. Bug reports and suggestions are welcome.

When reporting a problem, include:

- what you were doing
- what you expected to happen
- what actually happened
- the full error message
- your Windows version

[Open a GitHub issue](https://github.com/pieroboseta/Calypso/issues)
