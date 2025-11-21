docs:
	cd docs
	make html
isort:
	isort solarpi
typecheck:
	mypy solarpi --ignore-missing-imports
lintcheck:
	flake8 --ignore=E501,E203,W503  solarpi
reformat:
	black solarpi
test:
	pytest -v tests --cov atomdb --cov-report xml --asyncio-mode auto

precommit: isort reformat lintcheck typecheck

build:
	python build.py
