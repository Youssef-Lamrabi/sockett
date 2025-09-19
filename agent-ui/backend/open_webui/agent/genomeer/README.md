# from repo root
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]   # editable + dev tooling

# run the adapter server
genomeer-serve
# -> listening on http://localhost:8099/chat
