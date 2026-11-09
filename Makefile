PYTHON ?= python3

.PHONY: install test demo serve check

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m paypilot.demo

serve:
	$(PYTHON) -m paypilot

check: test demo
