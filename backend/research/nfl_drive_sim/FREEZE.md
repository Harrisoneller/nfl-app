# P2 freeze

QA passed P2 at `3e22d5b`. The freeze commit is `c1320f7299815de7f35bff50c9ddc114c10937d8`. That commit changes only the disclosure wording in the 2018 reports and the manifest lines for those files. It does not change the simulator.

`sim_config.py` sha256: `f60cd566e06e87f66d000f8ec8f3afc4bd4e3b8ed91711374c005fe0e3be7ecb`

2018 stays dev-touched. From this commit on, no hyperparameter and no entry in `sim_config.py` is retuned. A later document, including the P3 preregistration, does not move this freeze.
