Synthetic launcher regression fixture for the ZEN-53 protected-approval v1
candidate. These four files are never installed as a production launch tool.
Run isolated Python `-I -S -B -m unittest discover -s <this-directory> -p 'test_*.py' -q`
with a clean environment and private trusted `TMPDIR`.
The launcher source must exactly match the operator package's launcher source.
No live scope, Codex, network or host security operation is performed.
