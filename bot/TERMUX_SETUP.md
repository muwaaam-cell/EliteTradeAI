# Termux setup (Android)

This path reuses the Termux-installed `python-cryptography` package. A normal
isolated virtual environment hides that package, which is why importing
`cryptography` failed even after `pkg install python-cryptography`.

Run these commands from the repository's `bot/` directory. They create a
separate environment and do not delete or overwrite an existing `.venv`.

```sh
pkg update
pkg install python python-cryptography git openssl -y
python -m venv --system-site-packages .venv-termux
source .venv-termux/bin/activate
python -c "import cryptography; print('cryptography', cryptography.__version__)"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
PYTHONPATH=. python -m unittest discover -s tests -v
```

The dependency floor is `cryptography>=48.0.1`, which is sufficient for the
AES-256-GCM `AESGCM` API used by `WorkerTokenVault`. The virtual environment
must be created with `--system-site-packages` so it can see Termux's package.
Do not use `pip install --upgrade cryptography` on Termux: that can force a
source build and fail on Android.

If the import check fails, stop there; do not attempt broker login. Confirm
that `pkg install python-cryptography` completed and that the active Python
is the Termux interpreter (`which python`). Do not remove the existing
environment or local state.

After dependencies and tests pass, configure the local untracked `.env` and
encrypted token envelope as described in `README.md`. Keep all secrets on
the phone. This runner is still demo-only and market-data-only: it does not
submit orders, and a successful unit-test run does not prove Pepperstone
connectivity.
