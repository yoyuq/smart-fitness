# Contributing to Smart Fitness

Thanks for your interest in improving the Smart Fitness Guidance System. This document covers how to set up a local development environment, run the offline scoring test suite, and submit changes via pull requests.

For a high-level project overview, see [README.md](README.md). For deployment instructions (backend, ESP32-CAM firmware, Android app), see [DEPLOY.md](DEPLOY.md).

## Getting Started

### Prerequisites

- **Python 3.10+** — required for the backend and AI vision modules
- **Git** — to clone and contribute to the repository
- Optional: Arduino IDE 2.x (for ESP32-CAM firmware changes)
- Optional: Android Studio (for app changes)

### Clone the repository

The canonical repository is [yoyuq/smart-fitness](https://github.com/yoyuq/smart-fitness).

```bash
git clone https://github.com/yoyuq/smart-fitness.git
cd smart-fitness
```

If you are contributing from a fork, first fork the repository on GitHub (the fork will be created under your account as `your-username/smart-fitness`), then clone your fork:

```bash
git clone https://github.com/your-username/smart-fitness.git
cd smart-fitness
```

### Set up a Python virtual environment

From the repository root, create and activate a virtual environment:

```bash
python -m venv .venv
```

Activate it on your platform:

- **Linux / macOS:**
  ```bash
  source .venv/bin/activate
  ```
- **Windows (PowerShell):**
  ```powershell
  .venv\Scripts\Activate.ps1
  ```
- **Windows (Command Prompt):**
  ```cmd
  .venv\Scripts\activate.bat
  ```

### Backend dependencies

For full backend development (FastAPI, MediaPipe, pose estimation, LLM integrations), install the backend requirements:

```bash
pip install -r backend/requirements.txt
```

See [backend/requirements.txt](backend/requirements.txt) for the full dependency list.

## Running the Offline Scoring Tests

The repository includes a self-contained test suite for the rep scoring logic in `tests/test_rep_scorer.py`. These tests use **synthetic joint angles** — they do not require a camera, the backend server, or any AI model calls. You can run them offline with only `pytest` and `numpy`.

### Minimal install (offline scoring tests only)

```bash
python -m pip install pytest numpy
```

### Run the tests

From the repository root:

```bash
python -m pytest tests/test_rep_scorer.py -q
```

These tests validate:
- Standard squats score high with full depth
- Shallow squats are penalized on depth
- Bouncy/fast squats are penalized on control
- Asymmetric left/right knee angles reduce symmetry score
- Idle standing frames do not dilute session averages
- Switching exercises resets the scorer state

> **Note:** This test subset covers only the evidence-based rep quality scorer. It is **not** a full acceptance test for the hardware, camera pipeline, backend server, pose engine, or LLM coach integration.

## Branching and Commit Workflow

We use a simple feature-branch workflow.

### Create a branch

```bash
git checkout -b feature/your-feature-name
```

Use descriptive branch names. Prefixes like `feature/`, `fix/`, `docs/`, and `chore/` help reviewers understand scope at a glance.

### Make small, focused commits

Each commit should represent one logical change. Write clear commit messages that explain *what* and *why*, not just *how*.

```bash
git add <changed-files>
git commit -m "Brief summary of the change

More detailed explanation if needed. Wrap at 72 characters."
```

### Validate your changes

Before opening a pull request:

1. Run the offline scoring tests if your change touches `rep_scorer.py`, `exercise_detector.py`, or related scoring logic:
   ```bash
   python -m pytest tests/test_rep_scorer.py -q
   ```
2. **Report test results honestly.** Compare your branch's test output against the unchanged `main` branch (the baseline). Distinguish pre-existing failures on `main` from regressions your change introduces. Do not claim a test suite passed just because the command ran — include the actual output in your PR description.
3. For backend changes, start the server and check the health endpoint:
   ```bash
   cd backend
   python -m uvicorn main:app --host 0.0.0.0 --port 8080
   # In another terminal:
   curl http://localhost:8080/health
   ```
4. For documentation-only changes, verify links and readability locally.

## Pull Requests

When your change is ready:

1. Push your branch to your fork or the repository.
2. Open a pull request against the `main` branch.
3. Fill in the pull request template (if available) with:
   - A clear title and description of the change
   - The motivation and context
   - How you verified the change
   - Any related issues or discussions

Expect code review feedback. Be responsive — address comments, push follow-up commits, and re-request review when you're ready.
