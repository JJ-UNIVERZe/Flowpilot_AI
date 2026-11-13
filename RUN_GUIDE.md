# FlowPilot AI — Windows PowerShell Run Guide

This guide starts the local, fictional-data demo on Windows. The commands below
assume the project folder is `D:\LLM-Powered Agent`. If you cloned it elsewhere,
replace that path in each `Set-Location` command.

The project requires Python 3.10 or newer. The examples use the Windows Python
launcher (`py`).

## 1. Open PowerShell and enter the project folder

```powershell
Set-Location "D:\LLM-Powered Agent"
py --version
```

You should see a Python version of 3.10 or newer. If `py` is not recognized,
install Python for Windows with the Python launcher, then reopen PowerShell.

## 2. Create and activate a virtual environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

The prompt should now begin with `(.venv)`. If PowerShell says script execution
is disabled, allow activation only for this PowerShell process and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 3. Install project dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` also installs the runtime packages from
`requirements.txt`, including FastAPI, Uvicorn, Streamlit, and LangGraph. Wait
for pip to finish and return to the PowerShell prompt without an error.

## 4. Create the local environment file

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
} else {
    Write-Host ".env already exists; keeping it unchanged."
}
Test-Path .env
```

The last command should print `True`. `.env.example` contains placeholders,
not credentials. **This application does not automatically load `.env`**;
settings must be provided as process environment variables. Mock mode needs no
OpenAI key. The local reviewer key is set in the backend window in step 7.

## 5. Create and seed the SQLite database

There is no separate seed script. Run the database module's initializer:

```powershell
python -c "from database import create_database; create_database(); print('Seeded SQLite database: orders.db')"
```

You should see `Seeded SQLite database: orders.db`. The file is created in the
project root beside `database.py`. This command creates or migrates the schema
and inserts missing fictional sample rows; it does not reset or overwrite
existing order data.

## 6. Run the tests

```powershell
python -m unittest discover -v
```

The test names will scroll by. A successful run ends with output like:

```text
Ran <number> tests in <duration>
OK
```

The number and duration can change as tests are added. The tests use mock or
temporary data and do not require a live OpenAI key.

## 7. Start the FastAPI backend

Keep the first PowerShell window open for the backend. From the project folder,
activate the environment again if needed, set a local-only reviewer key, and
start the server:

```powershell
Set-Location "D:\LLM-Powered Agent"
.\.venv\Scripts\Activate.ps1
$env:REVIEW_API_KEY = "local-demo-review-key"
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

The terminal should show Uvicorn running at `http://127.0.0.1:8000`. Leave this
window running. The reviewer key above is only a local demo value; enter the
same value in the dashboard's reviewer-key field when reviewing a response.
Environment variables set this way apply only to this PowerShell window.

## 8. Start the Streamlit dashboard

Open a **second** PowerShell window. From that window run:

```powershell
Set-Location "D:\LLM-Powered Agent"
.\.venv\Scripts\Activate.ps1
streamlit run ui.py --server.address 127.0.0.1 --server.port 8501
```

The terminal should show a local URL at `http://127.0.0.1:8501`. Keep this
window running too. The dashboard connects to the API at
`http://127.0.0.1:8000` by default.

## 9. Open the API and dashboard in your browser

You can paste these URLs into your browser, or run the commands below from
another PowerShell window:

```powershell
Start-Process "http://127.0.0.1:8000/docs"
Start-Process "http://127.0.0.1:8501"
```

When both services are running:

- The API URL opens FastAPI's interactive documentation titled **FlowPilot AI**.
  The API health check at `http://127.0.0.1:8000/health` returns
  `{"status":"ok"}`.
- The dashboard opens a page titled **FlowPilot AI** with the **Workflow**,
  **Agent review**, **Evaluation**, **Review stats**, and **Agent metrics**
  tabs.
- The dashboard's reviewer key field should be set to
  `local-demo-review-key` to access protected review and statistics features.

To stop either service, focus its PowerShell window and press `Ctrl+C`.
