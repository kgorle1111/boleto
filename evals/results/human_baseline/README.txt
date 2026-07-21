Boleto human-baseline pack
6 degraded tickets (severity >= 0.5).

1. Give answer_sheet.csv + the PNGs to each reader (NOT _truth.json).
2. They transcribe every field they can read; blank = unreadable.
3. Score:  .venv-gemma/bin/python -m evals.human_baseline score answer_sheet_filled.csv
It emits the same metrics as the model eval, ready to sit beside the model column.
