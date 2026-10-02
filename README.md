# LLM-Assisted Quality Evaluation of User Stories

A final-year requirements-engineering application that combines INVEST evaluation, explainable quality signals, revision comparison, batch experiments and a Requirement Studio for decomposition and traceability.

## Run
1. `cd backend`
2. `python -m venv venv`
3. `venv\Scripts\Activate.ps1`
4. `pip install -r requirements.txt`
5. Set `OPENAI_API_KEY` only in your local terminal if LLM evaluation is enabled.
6. `python app.py`
7. Open `http://127.0.0.1:5000`

## Core innovation modules
- Explainable Story Quality DNA
- Requirement smell/risk analysis
- Revision comparator
- Batch Quality Experiment
- Requirement Studio: candidate story slices + traceability matrix

The system is decision support. Human product/development review remains the final validation step.
