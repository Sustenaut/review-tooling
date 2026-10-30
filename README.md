# review-tooling

Code for working with a corpus of signed facts and paper extracts, and the
review service for suggested concepts. **Code only: no data.** Gathered data
stays private until an explicit publishing step puts it somewhere public, so
this repository holds no registry, extract or object. Its tests run on made-up
data in `tests/fixtures/root/`.

| Part | What it does |
|---|---|
| `review_tooling/facts` | Composes and signs a facts registry (`corpus/registry.json`) and checks prose against it |
| `review_tooling/sources` | Adds, lints, checks, gathers and uploads paper extracts (`corpus/sources/`) |
| `review_tooling/suggest` | Has a model draft concepts for the extracts, signed as triage for review |
| `review-site/` | The review service (review.sustenaut.eu), where people review those suggestions |

## Using it

The tools read and write `corpus/` in the repository they run from (or
`$SOURCES_ROOT`), and that repository holds the data. Sustenaut/business-plan
installs them at a tag:

```sh
pip install "review-tooling @ git+https://github.com/Sustenaut/review-tooling@vX.Y.Z"
python -m review_tooling.facts lint
python -m review_tooling.sources check
```

The suggestion job also needs `review-tooling[suggest]`. Gathering and
signing need a `pub` from publet-cli.

A release is a tag on main, with the version in `pyproject.toml`.

## Testing

```sh
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python -r requirements.lock
.venv/bin/python -m pytest -q tests
```

Tests that need `pub` skip without one. The repository holding the real registry
tests its own data as well.
