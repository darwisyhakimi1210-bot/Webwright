# 🌐 Webwright — Complete Setup Guide (Zero to Hero)

> A friendly, step-by-step walkthrough for getting Webwright running on your machine, written for beginners.

---

## What is Webwright?

**Webwright** is a lightweight web-agent framework that gives an LLM a **terminal + Playwright browser** to solve web tasks. Instead of predicting one click at a time, the model writes and executes full Playwright scripts, saving screenshots and logs as evidence. Think of it as an **RPA (Robotic Process Automation) harness** where the "robot" is an AI model writing code.

---

## 🟢 Stage 1: Prerequisites

Before installing, make sure you have the following on your machine:

| Requirement | Version  | Check Command      |
| ----------- | -------- | ------------------ |
| Python      | 3.10+    | `python --version` |
| Git         | Any recent | `git --version`  |

> If either command fails or returns an older version, install/upgrade before continuing.

---

## 🟢 Stage 2: Installation (One-Time Setup)

### Step 1 — Install the package

```bash
# Clone the repository
git clone https://github.com/microsoft/Webwright.git
cd Webwright

# Install in editable mode (links to source, so edits are live)
pip install -e .

# Install Playwright Chromium browser
playwright install chromium
```

### Step 2 — Verify your setup

```bash
# Run the built-in doctor check
python -m webwright.run.cli doctor
```

The `doctor` command checks:

- ✅ Python version (3.10+)
- ✅ Playwright library installed
- ✅ Chromium browser available
- ✅ Screenshot capture working
- ⚠️ OpenAI API key (only needed if using OpenAI models)
- ✅ Plugin manifests

If anything shows ❌, fix it before moving to Stage 3.

---

## 🟢 Stage 3: Choose Your Model Backend

Webwright supports **3 model backends**. Pick one based on which API access you have:

### Option A — OpenAI (Default)

```bash
export OPENAI_API_KEY="sk-xxxxxxxxxxxx"
```

Config: `-c base.yaml -c model_openai.yaml`

### Option B — Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-xxxxxxxxxxxx"
```

Config: `-c base.yaml -c model_claude.yaml`

### Option C — OpenRouter (GPT-5 via OpenAI, but through OpenRouter)

```bash
export OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxx"
```

Config: `-c base.yaml -c model_openrouter.yaml`

### 💡 Which should I pick?

| Backend    | Best For                                                                |
| ---------- | ----------------------------------------------------------------------- |
| OpenAI     | Direct, fast, reliable (**recommended for beginners**)                   |
| Anthropic  | Stronger reasoning (Claude models)                                      |
| OpenRouter | Access GPT-5 and many different models through one API                  |

---

## 🟢 Stage 4: Understanding Config Files

Webwright uses a **stackable config system**. You layer YAML files like this:

```
       base.yaml              → Shared settings (timeouts, output dir, etc.)
              +
       local_browser.yaml     → Live browser mode (optional, for interactive browsing)
              +
       model_openai.yaml      → Which model/backend to use
              ↓
python -m webwright.run.cli -c base.yaml -c local_browser.yaml -c model_openai.yaml ...
```

### Config files available in `src/webwright/config/`

| File                       | Purpose                                              |
| -------------------------- | ---------------------------------------------------- |
| `base.yaml`                | Base settings for everything                         |
| `local_browser.yaml`       | Live interactive browser mode                        |
| `local_workspace.yaml`     | Workspace-based mode (saves scripts/logs)            |
| `model_openai.yaml`        | OpenAI backend (GPT models)                          |
| `model_claude.yaml`        | Anthropic backend (Claude models)                    |
| `model_openrouter.yaml`    | OpenRouter backend (many models)                     |
| `task_showcase.yaml`       | Dashboard mode for repeatable tasks                  |
| `persistent_browser.yaml`  | Persistent browser session mode                      |

---

## 🟢 Stage 5: Running Your First Task

### Quick Test (no API key needed for Claude Code skill mode)

The simplest first test is using Claude Code's built-in Webwright skill (no API key needed!):

```
/webwright:run Open example.com and report the title
```

### Full CLI Run (requires API key)

```bash
# Set your API key
export OPENAI_API_KEY="sk-xxxxxxxxxxxx"

# Run a simple task
python -m webwright.run.cli \
  -c base.yaml -c model_openai.yaml \
  -t "Open example.com and report the title" \
  --task-id demo \
  -o outputs/demo
```

### With a Starting URL

```bash
export OPENAI_API_KEY="sk-xxxxxxxxxxxx"

python -m webwright.run.cli \
  -c base.yaml -c model_openai.yaml \
  -t "Search for flights from SEA to JFK" \
  --start-url https://www.google.com/flights \
  --task-id flights_demo \
  -o outputs/demo
```

### Interactive Debug Mode (headed browser)

```bash
export OPENAI_API_KEY="sk-xxxxxxxxxxxx"

python -m webwright.run.cli \
  -c base.yaml -c model_openai.yaml \
  -t "Open example.com" \
  --task-id debug_demo \
  -o outputs/demo \
  --debug
```

> Adding `--debug` shows the actual browser window and devtools so you can watch what the agent does.

---

## 🟡 Stage 6: Understanding the Two Operating Modes

Webwright has **two distinct modes**:

### Mode 1: Workspace Mode (Default)

The model writes Python scripts to `final_script.py`, saves screenshots to `final_runs/run_1/`, and produces a complete reproducible artifact.

```bash
python -m webwright.run.cli -c base.yaml -c model_openai.yaml ...
```

**Output structure:**

```
outputs/demo_20260623_143052/
├── trajectory.json          # Full conversation log
├── final_runs/
│   └── run_1/
│       ├── final_script.py       # The reproducible script
│       ├── final_script_log.txt  # Action log
│       └── screenshots/
│           └── final_execution_1_action.png
└── runtime_errors.jsonl
```

### Mode 2: Live Browser Mode

The model drives a live browser interactively. No files saved. Good for quick exploration.

```bash
python -m webwright.run.cli -c base.yaml -c local_browser.yaml -c model_openai.yaml ...
```

In this mode:

- **No** `final_script.py` is created
- The model drives the browser directly
- Observations show ARIA snapshots and screenshots
- Faster for quick tasks

---

## 🟡 Stage 7: The Self-Reflection Tool

For workspace mode, Webwright has a built-in visual verification tool:

```bash
python -m webwright.tools.self_reflection \
  --config workspace/self_reflect_config.json \
  --workspace-dir "outputs/demo_xxx" \
  --output outputs/demo_xxx/final_runs/run_1/self_reflect_result.json
```

This:

- Scores each screenshot against your **critical points (CPs)**
- Aggregates all evidence
- Outputs `Status: success` or `Status: failure`

---

## 🟡 Stage 8: Web Agent Workflow (How the Model Thinks)

When you run a task, the model follows this loop:

```
┌─────────────────────────────────────┐
│  1. PLAN — Parse task into critical  │
│     points (CPs), write plan.md     │
├─────────────────────────────────────┤
│  2. EXPLORE — Run scratch scripts   │
│     to discover page structure      │
├─────────────────────────────────────┤
│  3. AUTHOR — Write final_script.py  │
│     with instrumented screenshots   │
├─────────────────────────────────────┤
│  4. EXECUTE — Run the script once   │
├─────────────────────────────────────┤
│  5. SELF-REFLECT — Verify each CP   │
│     against screenshots             │
├─────────────────────────────────────┤
│  6. DONE — If all CPs verified      │
└─────────────────────────────────────┘
```

---

## 🔴 Stage 9: Common CLI Flags Reference

| Flag           | Example                                          | Purpose                       |
| -------------- | ------------------------------------------------ | ----------------------------- |
| `-c`           | `-c base.yaml -c model_openai.yaml`              | Stack config files            |
| `-t`           | `-t "Search for flights..."`                     | Task description              |
| `--start-url`  | `--start-url https://google.com`                 | Starting page                 |
| `--task-id`    | `--task-id my_flights`                           | Named output folder           |
| `-o`           | `-o outputs/my_run`                              | Output directory              |
| `--debug`      | `--debug`                                        | Headed browser + devtools     |

---

## 🔴 Stage 10: Real-World Example Commands

### Extract data from a website

```bash
export OPENAI_API_KEY="sk-xxxx"
python -m webwright.run.cli \
  -c base.yaml -c model_openai.yaml \
  -t "Find all job listings for 'Python Developer' on indeed.com and list the top 5 with company name, title, and salary if available" \
  --start-url https://www.indeed.com \
  --task-id job_scraper \
  -o outputs/jobs
```

### Fill out a web form

```bash
export ANTHROPIC_API_KEY="sk-ant-xxxx"
python -m webwright.run.cli \
  -c base.yaml -c model_claude.yaml \
  -t "Go to github.com, sign up with email 'test@example.com' and username 'testuser123'" \
  --task-id github_signup \
  -o outputs/github
```

### Use OpenRouter (access many models)

```bash
export OPENROUTER_API_KEY="sk-or-v1-xxxx"
python -m webwright.run.cli \
  -c base.yaml -c model_openrouter.yaml \
  -t "Open example.com and report the title" \
  --task-id openrouter_test \
  -o outputs/test
```

---

## 🟢 Quick Command Reference Card

```bash
# === INSTALL ===
git clone https://github.com/microsoft/Webwright.git
cd Webwright
pip install -e .
playwright install chromium

# === VERIFY ===
python -m webwright.run.cli doctor

# === RUN (OpenAI) ===
export OPENAI_API_KEY="sk-xxxx"
python -m webwright.run.cli \
  -c base.yaml -c model_openai.yaml \
  -t "Your task here" \
  --start-url https://example.com \
  --task-id my_task \
  -o outputs/my_task

# === RUN (Claude) ===
export ANTHROPIC_API_KEY="sk-ant-xxxx"
python -m webwright.run.cli \
  -c base.yaml -c model_claude.yaml \
  -t "Your task here" \
  --task-id my_task \
  -o outputs/my_task

# === RUN (OpenRouter) ===
export OPENROUTER_API_KEY="sk-or-v1-xxxx"
python -m webwright.run.cli \
  -c base.yaml -c model_openrouter.yaml \
  -t "Your task here" \
  --task-id my_task \
  -o outputs/my_task

# === DEBUG (see browser) ===
python -m webwright.run.cli \
  -c base.yaml -c model_openai.yaml \
  -t "Your task here" \
  --task-id debug \
  -o outputs/debug \
  --debug
```

---

*Happy automating! 🚀*