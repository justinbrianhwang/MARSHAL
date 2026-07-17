"""MARSHAL Town03 closed-loop benchmark.

A reproducible 9-scenario authority-conflict driving benchmark on
CARLA Town03, designed for fair comparison of:
  - End-to-end models (TransFuser, InterFuser, TCP)
  - VLM-based agents (e.g. CLIP, GPT-4V, LLaVA, …)
  - Baselines (pure-pursuit + scripted reactions)

Architecture:
  - Fixed waypoint route + 9 stations (route.json / stations.json)
  - Pluggable controllers (controllers/*.py)
  - Standard sensor suite (sensors.py)
  - Per-station scenario manager (scenario_manager.py)
  - Per-station verdict + overall driving score (evaluator.py)
"""
